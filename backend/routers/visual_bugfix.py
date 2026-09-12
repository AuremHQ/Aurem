"""
routers/visual_bugfix.py — Multimodal Visual Bug-Fix pipeline,
Phase 1 (upload/storage) + Phase 2 (vision analysis + codebase
grounding). Phase 3 (patch generation + headless preview) and Phase 4
(before/after comparison UI) are explicitly NOT built yet — founder
approved a phased rollout (2026-09-12), Phase 1+2 first.

Flow:
  1. POST /upload           — client sends the raw screenshot file.
     We validate it, persist it via Emergent Object Storage, and
     record a `visual_bug_reports` doc (14-day TTL — see
     services/visual_bugfix_storage.py's module docstring for exactly
     what that TTL does and doesn't cover).
  2. GET  /{id}/image        — serves the stored bytes back (used for
     the composer thumbnail AND the eventual before/after card).
  3. POST /{id}/analyze      — ONE Gemini vision call (Cost Guard: max
     1 vision + 1 code-gen call per run) extracting visual_defects /
     extracted_strings / inferred_dom_elements / suggested_patterns,
     then correlates those against the user's CONNECTED project repo
     using the existing `search_repo` (grep, deterministic — matches
     the founder's "Deterministic Localization" requirement) and
     `semantic_search_repo` (GitHub Code Search + local TF-IDF
     fallback) tools from services/local_tools.py — reused as-is, no
     new indexing system built.
"""
from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from PIL import Image

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.bin_context import build_bin_context
from services.visual_bugfix_storage import get_object, put_object
from services.visual_bugfix_vision import analyze_screenshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/visual-bugfix", tags=["visual-bugfix"])

_ALLOWED_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MAX_BYTES = 10 * 1024 * 1024
_TTL_SECONDS = 14 * 24 * 3600


async def _ensure_ttl_index(db) -> None:
    """Create a TTL index on `visual_bug_reports.created_at` (idempotent,
    mirrors routers/scaffold.py's `_ensure_ttl_index`). This is the ONLY
    real "auto-expire" mechanism here — see visual_bugfix_storage.py's
    module docstring for why the backing object-storage bytes
    themselves can't be deleted."""
    try:
        await db.visual_bug_reports.create_index(
            "created_at", expireAfterSeconds=_TTL_SECONDS, name="visual_bug_reports_ttl",
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] TTL index create failed: %r", e)


async def _resolve_user(authorization: Optional[str]) -> dict:
    payload = await current_dev(authorization)
    if not payload or not payload.get("user_id"):
        raise HTTPException(401, "Invalid token")
    db = get_db()
    user = await db.dev_users.find_one({"user_id": payload["user_id"]})
    if not user:
        raise HTTPException(401, "User not found")
    return user


async def _get_owned_report(report_id: str, user_id: str) -> dict:
    db = get_db()
    doc = await db.visual_bug_reports.find_one({"report_id": report_id})
    if not doc:
        raise HTTPException(404, "Report not found")
    if doc.get("user_id") != user_id:
        raise HTTPException(404, "Report not found")
    return doc


@router.post("/upload")
async def upload_screenshot(
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    user = await _resolve_user(authorization)
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported image type: {content_type or 'unknown'} "
                                  "(png, jpeg, webp only)")
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(400, f"Image too large ({len(data)} bytes, max {_MAX_BYTES})")
    if not data:
        raise HTTPException(400, "Empty file")

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except Exception:
        raise HTTPException(400, "Could not read image — file may be corrupt")

    file_hash = hashlib.sha256(data).hexdigest()
    report_id = uuid.uuid4().hex
    ext = _ALLOWED_TYPES[content_type]
    storage_path = f"visual-bugfix/{user['user_id']}/{report_id}.{ext}"

    try:
        await put_object(storage_path, data, content_type)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] storage upload failed: %r", e)
        raise HTTPException(502, "Could not store image — please try again")

    now = datetime.now(timezone.utc)
    db = get_db()
    await _ensure_ttl_index(db)
    await db.visual_bug_reports.insert_one({
        "report_id":     report_id,
        "user_id":       user["user_id"],
        "project_id":    project_id,
        "storage_path":  storage_path,
        "content_type":  content_type,
        "file_hash":     file_hash,
        "width":         width,
        "height":        height,
        "status":        "uploaded",
        "vision_analysis": None,
        "candidate_files": [],
        "created_at":    now,
    })
    return {
        "ok": True,
        "id": report_id,
        "image_url": f"/api/aurem-dev/visual-bugfix/{report_id}/image",
        "width": width,
        "height": height,
        "file_hash": file_hash,
    }


@router.get("/{report_id}/image")
async def get_screenshot(
    report_id: str,
    authorization: Optional[str] = Header(None),
    auth: Optional[str] = Query(None),
):
    # `<img src>` can't set custom headers — accept the token as a
    # query param too, same convention as routers/github_app.py's
    # install-redirect route.
    user = await _resolve_user(authorization or (f"Bearer {auth}" if auth else None))
    doc = await _get_owned_report(report_id, user["user_id"])
    try:
        data, content_type = await get_object(doc["storage_path"])
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] storage fetch failed: %r", e)
        raise HTTPException(502, "Could not load image")
    return Response(content=data, media_type=content_type or doc.get("content_type"))


@router.post("/{report_id}/analyze")
async def analyze_screenshot_endpoint(
    report_id: str,
    body: dict,
    authorization: Optional[str] = Header(None),
):
    user = await _resolve_user(authorization)
    doc = await _get_owned_report(report_id, user["user_id"])
    bug_description = (body or {}).get("bug_description") or ""
    project_id = (body or {}).get("project_id") or doc.get("project_id")

    db = get_db()
    await db.visual_bug_reports.update_one(
        {"report_id": report_id}, {"$set": {"status": "analyzing"}},
    )

    try:
        image_bytes, content_type = await get_object(doc["storage_path"])
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] storage fetch failed pre-analysis: %r", e)
        await db.visual_bug_reports.update_one(
            {"report_id": report_id}, {"$set": {"status": "failed"}},
        )
        raise HTTPException(502, "Could not load the image to analyze")

    vision = await analyze_screenshot(
        image_bytes, content_type or doc.get("content_type", "image/png"), bug_description,
    )

    candidate_files: list[dict] = []
    if vision.get("ok") and project_id:
        candidate_files = await _correlate_codebase(
            user["user_id"], project_id,
            vision.get("extracted_strings") or [],
            vision.get("suggested_patterns") or [],
        )

    status = "analyzed" if vision.get("ok") else "failed"
    await db.visual_bug_reports.update_one(
        {"report_id": report_id},
        {"$set": {
            "status": status,
            "bug_description": bug_description,
            "project_id": project_id,
            "vision_analysis": vision,
            "candidate_files": candidate_files,
        }},
    )

    return {
        "ok": vision.get("ok", False),
        "id": report_id,
        "status": status,
        "error": vision.get("error"),
        "visual_defects": vision.get("visual_defects", []),
        "extracted_strings": vision.get("extracted_strings", []),
        "inferred_dom_elements": vision.get("inferred_dom_elements", []),
        "suggested_patterns": vision.get("suggested_patterns", []),
        "candidate_files": candidate_files,
    }


async def _correlate_codebase(user_id: str, project_id: str,
                               extracted_strings: list[str],
                               suggested_patterns: list[str]) -> list[dict]:
    """Deterministic Localization (founder's quality gate): combine
    literal-text grep (`search_repo`, exact OCR'd strings — highest
    confidence) with concept search (`semantic_search_repo`, GitHub
    Code Search + local TF-IDF fallback — lower confidence) instead of
    unstructured brute-force searching. Reuses the SAME tools the
    agent's own Loop Mode uses to read the connected repo — no new
    indexing system built for this."""
    from services.local_tools import search_repo, semantic_search_repo

    db = get_db()
    try:
        bin_ctx = await build_bin_context(user_id, project_id=project_id, db=db)
    except Exception as e:                                    # noqa: BLE001
        # build_bin_context raises HTTPException(400/403) for a
        # missing/foreign/unconnected project — codebase correlation
        # is a nice-to-have on top of the vision analysis, never a
        # reason to fail the whole /analyze call.
        logger.info("[visual_bugfix] codebase correlation skipped (no usable project): %r", e)
        return []
    ctx = {"user_id": user_id, "project_id": project_id, "bin_ctx": bin_ctx}

    scored: dict[str, dict] = {}

    def _bump(path: str, delta: float, source: str):
        row = scored.setdefault(path, {"path": path, "confidence": 0.0, "sources": []})
        row["confidence"] = min(1.0, row["confidence"] + delta)
        if source not in row["sources"]:
            row["sources"].append(source)

    # Highest confidence: the EXACT text visible in the screenshot
    # literally appears in a source file.
    for s in extracted_strings[:5]:
        if len(s) < 3:
            continue
        try:
            r = await search_repo(ctx, {"pattern": s, "max": 5})
        except Exception as e:                                # noqa: BLE001
            logger.debug("[visual_bugfix] search_repo failed for %r: %r", s, e)
            continue
        if r.get("ok"):
            for m in (r.get("matches") or [])[:5]:
                _bump(m.get("file"), 0.35, "extracted_text_match")

    # Lower confidence: conceptual/component-name search.
    for p in suggested_patterns[:5]:
        clean = p.strip("*").strip()
        if len(clean) < 3:
            continue
        try:
            r = await semantic_search_repo(ctx, {"query": clean, "max": 5})
        except Exception as e:                                # noqa: BLE001
            logger.debug("[visual_bugfix] semantic_search_repo failed for %r: %r", p, e)
            continue
        if r.get("ok"):
            for h in (r.get("results") or [])[:5]:
                _bump(h.get("path"), 0.15, "semantic_match")

    ranked = sorted(scored.values(), key=lambda x: x["confidence"], reverse=True)
    return ranked[:8]
