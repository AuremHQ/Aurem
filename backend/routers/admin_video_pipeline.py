"""
routers/admin_video_pipeline.py — 2026-09-10, ORA Chat "Video
generation" mode: Deepgram Aura-2 TTS + fal.ai LatentSync lip-sync
(Emergent Universal Key) + ffmpeg hybrid compositor, orchestrated by
services/video_pipeline/pipeline.py as a background asyncio task
(same pattern as routers/fix_pipeline.py).

  GET    /admin/video/secrets            — key state (never returns raw keys)
  PUT    /admin/video/secrets            — save/rotate the Deepgram key and/or a BYOK fal.ai key
  DELETE /admin/video/secrets/fal-key    — remove the BYOK fal.ai key, fall back to Universal Key
  GET    /admin/video/house-rules        — house rules text + enforcement mode
  PUT    /admin/video/house-rules        — save rules text and/or mode
  POST   /admin/video/avatars            — add a photo/clip to the avatar library
  GET    /admin/video/avatars            — list this admin's avatar library
  DELETE /admin/video/avatars/{avatar_id}— remove one avatar
  POST   /admin/video/jobs               — start a new video-gen job (avatar_id required)
  GET    /admin/video/jobs/{job_id}      — poll one job
  GET    /admin/video/jobs               — list this admin's recent jobs
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from cto_services.auth import current_dev, require_admin_dep
from cto_services.db import get_db
from services.llm_config_store import EncryptionNotConfigured
from services.video_pipeline import house_rules, house_rules_check, pipeline, secrets_store, storage

router = APIRouter(
    prefix="/admin/video", tags=["Admin · Video generation"],
    dependencies=[Depends(require_admin_dep)],
)

MAX_AVATAR_BYTES = 60 * 1024 * 1024
MAX_AVATARS_PER_ADMIN = 8
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
VIDEO_TYPES = {"video/mp4", "video/quicktime"}


class UpdateSecretsBody(BaseModel):
    deepgram_api_key: Optional[str] = Field(None, max_length=500)
    fal_api_key: Optional[str] = Field(None, max_length=500)


class UpdateHouseRulesBody(BaseModel):
    rules_text: Optional[str] = Field(None, max_length=4000)
    enforcement_mode: Optional[str] = Field(None, pattern="^(block|rewrite)$")


class CreateJobBody(BaseModel):
    script: str = Field(min_length=1, max_length=2000)
    avatar_id: str


@router.get("/secrets")
async def read_secrets() -> dict:
    return await secrets_store.get_secrets_public(get_db())


@router.put("/secrets")
async def update_secrets(body: UpdateSecretsBody,
                           authorization: Optional[str] = Header(None)) -> dict:
    if not body.deepgram_api_key and not body.fal_api_key:
        raise HTTPException(400, "Provide a Deepgram key and/or a fal.ai key to update")
    db = get_db()
    user = await current_dev(authorization)
    try:
        return await secrets_store.set_secrets(
            db, deepgram_api_key=body.deepgram_api_key, fal_api_key=body.fal_api_key,
            updated_by=user.get("user_id"))
    except EncryptionNotConfigured as e:
        raise HTTPException(503, str(e))


@router.delete("/secrets/fal-key")
async def remove_fal_key(authorization: Optional[str] = Header(None)) -> dict:
    """Switch back to the Emergent Universal Key fallback for lip-sync."""
    db = get_db()
    user = await current_dev(authorization)
    return await secrets_store.clear_fal_key(db, updated_by=user.get("user_id"))


@router.get("/house-rules")
async def read_house_rules() -> dict:
    return await house_rules.get_rules(get_db())


@router.put("/house-rules")
async def update_house_rules(body: UpdateHouseRulesBody,
                               authorization: Optional[str] = Header(None)) -> dict:
    if body.rules_text is None and body.enforcement_mode is None:
        raise HTTPException(400, "Provide rules_text and/or enforcement_mode to update")
    db = get_db()
    user = await current_dev(authorization)
    return await house_rules.set_rules(
        db, rules_text=body.rules_text, enforcement_mode=body.enforcement_mode,
        updated_by=user.get("user_id"))


def _read_upload(raw: bytes, content_type: str) -> tuple[str, str]:
    ctype = (content_type or "").lower()
    if ctype in IMAGE_TYPES:
        return "image", ctype
    if ctype in VIDEO_TYPES:
        return "video", ctype
    raise HTTPException(415, "Use a PNG/JPEG/WEBP photo or an MP4/MOV clip")


@router.post("/avatars")
async def add_avatar(file: UploadFile = File(...),
                       authorization: Optional[str] = Header(None)) -> dict:
    db = get_db()
    user = await current_dev(authorization)
    admin_id = user["user_id"]

    existing_count = await db.video_pipeline_avatars.count_documents({"admin_id": admin_id})
    if existing_count >= MAX_AVATARS_PER_ADMIN:
        raise HTTPException(409, f"Avatar library full — delete one first (max {MAX_AVATARS_PER_ADMIN})")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty upload")
    if len(raw) > MAX_AVATAR_BYTES:
        raise HTTPException(413, f"File too large — max {MAX_AVATAR_BYTES // (1024 * 1024)}MB")
    kind, ctype = _read_upload(raw, file.content_type)

    ext = ".mp4" if kind == "video" else ("." + ctype.split("/")[-1])
    avatar_id = uuid.uuid4().hex
    key = f"{storage.PREFIX}avatars/{admin_id}/{avatar_id}{ext}"
    storage.upload_bytes(raw, key, ctype)

    label = (file.filename or "avatar").rsplit(".", 1)[0][:60] or "avatar"
    doc = {
        "avatar_id": avatar_id, "admin_id": admin_id, "r2_key": key, "kind": kind,
        "content_type": ctype, "label": label, "uploaded_at": time.time(),
    }
    await db.video_pipeline_avatars.insert_one(doc)
    return {"avatar_id": avatar_id, "kind": kind, "label": label,
            "preview_url": storage.presign_get(key, expires_in=3600)}


@router.get("/avatars")
async def list_avatars(authorization: Optional[str] = Header(None)) -> dict:
    db = get_db()
    user = await current_dev(authorization)
    cur = db.video_pipeline_avatars.find(
        {"admin_id": user["user_id"]}).sort("uploaded_at", -1)
    avatars = [
        {"avatar_id": d["avatar_id"], "kind": d["kind"], "label": d.get("label", "avatar"),
         "preview_url": storage.presign_get(d["r2_key"], expires_in=3600)}
        async for d in cur
    ]
    return {"avatars": avatars}


@router.delete("/avatars/{avatar_id}")
async def delete_avatar(avatar_id: str, authorization: Optional[str] = Header(None)) -> dict:
    db = get_db()
    user = await current_dev(authorization)
    doc = await db.video_pipeline_avatars.find_one(
        {"avatar_id": avatar_id, "admin_id": user["user_id"]})
    if not doc:
        raise HTTPException(404, "Avatar not found")
    storage.delete(doc["r2_key"])
    await db.video_pipeline_avatars.delete_one({"_id": doc["_id"]})
    return {"deleted": True}


@router.post("/jobs", status_code=202)
async def create_job(body: CreateJobBody,
                       authorization: Optional[str] = Header(None)) -> dict:
    db = get_db()
    user = await current_dev(authorization)
    admin_id = user["user_id"]

    secrets = await secrets_store.get_secrets_public(db)
    if not secrets["deepgram_configured"]:
        raise HTTPException(
            409, "Add your Deepgram key in Admin Settings → Video generation keys first")

    avatar = await db.video_pipeline_avatars.find_one(
        {"avatar_id": body.avatar_id, "admin_id": admin_id})
    if not avatar:
        raise HTTPException(404, "Avatar not found — pick one from your library")

    rules = await house_rules.get_rules(db)
    verdict = await house_rules_check.evaluate(
        body.script.strip(), rules["rules_text"], rules["enforcement_mode"])
    if verdict.blocked:
        raise HTTPException(422, f"Blocked by house rules: {verdict.reason}")

    job_id = pipeline.new_job_id()
    doc = {
        "job_id": job_id, "admin_id": admin_id,
        "script": verdict.final_script, "original_script": body.script.strip(),
        "avatar_r2_key": avatar["r2_key"], "avatar_kind": avatar["kind"],
        "status": "queued", "stage_detail": None, "error": None,
        "video_r2_key": None, "duration_s": None,
        "created_at": time.time(), "updated_at": time.time(),
    }
    await db.video_pipeline_jobs.insert_one(doc)
    asyncio.create_task(pipeline.run_job(db, job_id))
    return {"job_id": job_id, "status": "queued"}


def _public_job(doc: dict) -> dict:
    video_url = None
    if doc.get("video_r2_key"):
        video_url = storage.presign_get(doc["video_r2_key"], expires_in=3600)
    return {
        "job_id": doc["job_id"], "script": doc["script"],
        "was_rewritten": doc.get("script") != doc.get("original_script", doc.get("script")),
        "status": doc["status"], "stage_detail": doc.get("stage_detail"),
        "error": doc.get("error"), "video_url": video_url,
        "duration_s": doc.get("duration_s"), "created_at": doc.get("created_at"),
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, authorization: Optional[str] = Header(None)) -> dict:
    db = get_db()
    user = await current_dev(authorization)
    doc = await db.video_pipeline_jobs.find_one(
        {"job_id": job_id, "admin_id": user["user_id"]})
    if not doc:
        raise HTTPException(404, "Job not found")
    return _public_job(doc)


@router.get("/jobs")
async def list_jobs(authorization: Optional[str] = Header(None)) -> dict:
    db = get_db()
    user = await current_dev(authorization)
    cur = db.video_pipeline_jobs.find(
        {"admin_id": user["user_id"]}).sort("created_at", -1).limit(20)
    return {"jobs": [_public_job(doc) async for doc in cur]}

