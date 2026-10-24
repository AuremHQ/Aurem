"""
routers/admin_rea.py — admin-only "reverse engineer this website" chat
skill (github.com/morluto/rea), triggered from ORA chat via the
`/reverse-engineer <url>` slash command (see frontend SLASH_COMMANDS,
adminOnly: true). See services/rea_service.py for the full scope note
(frontend files are real/original text; "backend" is observed API
traffic, never actual server source — nothing server-side is ever
transmitted to a browser).

  POST /admin/rea/analyze            start a job for one URL
  GET  /admin/rea/jobs/{job_id}      status + file list (no file text)
  GET  /admin/rea/jobs/{job_id}/file/{sha256}   one file's real text

Router-level `Depends(require_admin_dep)` gates every route here (same
pattern as routers/admin_pricing.py) — founder accounts always pass;
a non-founder admin-team member would need a registry entry, which
none exists for here yet, so today this is founder-only in practice.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import require_admin_dep
from cto_services.db import require_db
from services.rea_service import ReaValidationError, start_job, validate_target_url

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/rea", tags=["Admin-REA"],
    dependencies=[Depends(require_admin_dep)],
)


class AnalyzeBody(BaseModel):
    url: str = Field(..., max_length=2048)


@router.post("/analyze")
async def analyze(body: AnalyzeBody, admin=Depends(require_admin_dep)):
    db = require_db()
    try:
        url = validate_target_url(body.url)
    except ReaValidationError as e:
        raise HTTPException(400, str(e))

    existing = await db.rea_jobs.find_one(
        {"requested_by": admin["user_id"], "status": "running"},
        {"job_id": 1, "url": 1},
    )
    if existing:
        raise HTTPException(
            409,
            f"Already reverse-engineering {existing['url']} (job {existing['job_id']}) "
            "— wait for it to finish before starting another.",
        )

    job_id = await start_job(db, url=url, requested_by=admin["user_id"])
    return {"job_id": job_id, "url": url, "status": "running"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    db = require_db()
    doc = await db.rea_jobs.find_one({"job_id": job_id}, {"_id": 0, "file_text_by_sha256": 0})
    if not doc:
        raise HTTPException(404, "Job not found")
    return doc


@router.get("/jobs/{job_id}/file/{sha256}")
async def get_job_file(job_id: str, sha256: str):
    db = require_db()
    doc = await db.rea_jobs.find_one(
        {"job_id": job_id}, {"_id": 0, "file_text_by_sha256": 1, "files": 1},
    )
    if not doc:
        raise HTTPException(404, "Job not found")
    text = (doc.get("file_text_by_sha256") or {}).get(sha256)
    if text is None:
        raise HTTPException(404, "File not found in this job")
    meta = next((f for f in doc.get("files", []) if f.get("sha256") == sha256), {})
    return {"sha256": sha256, "text": text, **meta}
