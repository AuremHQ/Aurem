"""
routers/admin_video_pipeline.py — 2026-09-10, ORA Chat "Video
generation" mode: Deepgram Aura-2 TTS + fal.ai LatentSync lip-sync
(Emergent Universal Key) + ffmpeg hybrid compositor, orchestrated by
services/video_pipeline/pipeline.py as a background asyncio task
(same pattern as routers/fix_pipeline.py).

  GET  /admin/video/secrets            — key state (never returns raw keys)
  PUT  /admin/video/secrets            — save/rotate the Deepgram key
  POST /admin/video/avatar             — upload founder avatar (photo or mp4 loop)
  GET  /admin/video/avatar             — current avatar (fresh presigned preview URL)
  POST /admin/video/jobs               — start a new video-gen job
  GET  /admin/video/jobs/{job_id}      — poll one job
  GET  /admin/video/jobs               — list this admin's recent jobs
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
from services.video_pipeline import pipeline, secrets_store, storage

router = APIRouter(
    prefix="/admin/video", tags=["Admin · Video generation"],
    dependencies=[Depends(require_admin_dep)],
)

MAX_AVATAR_BYTES = 60 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
VIDEO_TYPES = {"video/mp4", "video/quicktime"}


class UpdateSecretsBody(BaseModel):
    deepgram_api_key: Optional[str] = Field(None, max_length=500)


class CreateJobBody(BaseModel):
    script: str = Field(min_length=1, max_length=2000)


@router.get("/secrets")
async def read_secrets() -> dict:
    return await secrets_store.get_secrets_public(get_db())


@router.put("/secrets")
async def update_secrets(body: UpdateSecretsBody,
                           authorization: Optional[str] = Header(None)) -> dict:
    if not body.deepgram_api_key:
        raise HTTPException(400, "Provide a Deepgram API key to update")
    db = get_db()
    user = await current_dev(authorization)
    try:
        return await secrets_store.set_secrets(
            db, deepgram_api_key=body.deepgram_api_key, updated_by=user.get("user_id"))
    except EncryptionNotConfigured as e:
        raise HTTPException(503, str(e))


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...),
                          authorization: Optional[str] = Header(None)) -> dict:
    db = get_db()
    user = await current_dev(authorization)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty upload")
    if len(raw) > MAX_AVATAR_BYTES:
        raise HTTPException(413, f"File too large — max {MAX_AVATAR_BYTES // (1024 * 1024)}MB")
    ctype = (file.content_type or "").lower()
    if ctype in IMAGE_TYPES:
        kind = "image"
    elif ctype in VIDEO_TYPES:
        kind = "video"
    else:
        raise HTTPException(415, "Use a PNG/JPEG/WEBP photo or an MP4/MOV clip")

    admin_id = user["user_id"]
    ext = ".mp4" if kind == "video" else ("." + ctype.split("/")[-1])
    key = f"{storage.PREFIX}avatars/{admin_id}/{uuid.uuid4().hex}{ext}"
    storage.upload_bytes(raw, key, ctype)

    doc = {"admin_id": admin_id, "r2_key": key, "kind": kind,
           "content_type": ctype, "uploaded_at": time.time()}
    await db.video_pipeline_avatars.replace_one({"admin_id": admin_id}, doc, upsert=True)
    return {"kind": kind, "preview_url": storage.presign_get(key, expires_in=3600)}


@router.get("/avatar")
async def get_avatar(authorization: Optional[str] = Header(None)) -> dict:
    db = get_db()
    user = await current_dev(authorization)
    doc = await db.video_pipeline_avatars.find_one({"admin_id": user["user_id"]})
    if not doc:
        return {"configured": False}
    return {"configured": True, "kind": doc["kind"],
            "preview_url": storage.presign_get(doc["r2_key"], expires_in=3600)}


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

    avatar = await db.video_pipeline_avatars.find_one({"admin_id": admin_id})
    if not avatar:
        raise HTTPException(409, "Upload a founder photo or video loop first")

    job_id = pipeline.new_job_id()
    doc = {
        "job_id": job_id, "admin_id": admin_id, "script": body.script.strip(),
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
        "job_id": doc["job_id"], "script": doc["script"], "status": doc["status"],
        "stage_detail": doc.get("stage_detail"), "error": doc.get("error"),
        "video_url": video_url, "duration_s": doc.get("duration_s"),
        "created_at": doc.get("created_at"),
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
