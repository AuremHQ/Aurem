"""
routers/deploy_share.py — Aurem Deploy Phase 2 (2026-11-21).

POST /api/deploy/share — accepts a deploy failure from the Aurem
Deployer and drops it into the founder's ORA Chat inbox as a new
session, so a failed deploy shows up in chat instead of only in the
deployer's own log.

Mounted with a bare prefix (no internal APIRouter(prefix=...)) under
``app.include_router(deploy_share_router, prefix="/api")`` in main.py —
same pattern as routers/harness.py — so the final path is exactly
``POST /api/deploy/share``, matching the deployer's share contract
(deployer builds the payload; see aurem-deploy/docs/DESIGN.md,
"ORA share endpoint").

Auth: the existing ORA admin gate (``cto_services.auth.require_admin``,
Bearer JWT) — the same gate as every ``/api/aurem-dev/ora-chat/*``
endpoint. The deployer must present a founder/admin JWT. No new auth
scheme was invented for this endpoint.

Contract:
  POST /api/deploy/share
  Authorization: Bearer <founder/admin JWT>
  Request JSON:
    { "project": str, "deploy_id": str, "sha": str, "status": str,
      "failed_step": str = "", "predicted_reason": str = "",
      "suggested_fix": str = "", "log_tail": str = "" }
  Success: 200 {"thread_id": str, "title": str, "delivered": true}
    ("thread_id" is the ORA Chat session_id; "title" is the session
    title as stored — create_session truncates it to 80 chars.)
  Errors: 401 without/invalid auth, 400 on a bad payload
    (missing/empty required field, wrong type, malformed JSON),
    503 when the database is unavailable.

Notes:
  - The 400 (not FastAPI's default 422) is deliberate: the deployer
    treats any 4xx as "share rejected, keep the payload for copy",
    and the contract promises 400 for a bad payload. Validation
    therefore runs explicitly against the Pydantic model instead of
    through a body parameter.
  - Only this router + its test + the two main.py wiring lines are
    touched. No scorer, threshold, weight, or other ORA code.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from cto_services.auth import require_admin
from services.ora_chat import session as ora_session

logger = logging.getLogger(__name__)

router = APIRouter()

_REQUIRED_FIELDS = ("project", "deploy_id", "sha", "status")

# Caps keep a hostile/oversized deployer payload from bloating the
# chat session. The log tail is the only large field; 8000 chars is
# enough for a failure diagnosis and matches the deployer's own tail.
_MAX_LOG_TAIL_CHARS = 8000


class DeployShareBody(BaseModel):
    project: str = Field(..., min_length=1, max_length=128)
    deploy_id: str = Field(..., min_length=1, max_length=128)
    sha: str = Field(..., min_length=1, max_length=128)
    status: str = Field(..., min_length=1, max_length=32)
    failed_step: str = Field(default="", max_length=256)
    predicted_reason: str = Field(default="", max_length=2000)
    suggested_fix: str = Field(default="", max_length=2000)
    log_tail: str = Field(default="", max_length=_MAX_LOG_TAIL_CHARS)


def _build_message(body: DeployShareBody) -> str:
    """First message of the inbox thread — the whole failure context."""
    lines = [
        f"Deploy failed: {body.project} #{body.deploy_id}",
        "",
        f"Project: {body.project}",
        f"Deploy ID: {body.deploy_id}",
        f"Commit: {body.sha}",
        f"Status: {body.status}",
        f"Failed step: {body.failed_step or 'unknown'}",
        f"Predicted reason: {body.predicted_reason or 'unknown — see the log tail'}",
        f"Suggested fix: {body.suggested_fix or 'none suggested — see the log tail'}",
        "",
        "Log tail:",
        "```",
        body.log_tail or "(no log tail)",
        "```",
    ]
    return "\n".join(lines)


@router.post("/deploy/share")
async def share_deploy_failure(request: Request,
                               authorization: Optional[str] = Header(None)):
    # Same admin gate as the ORA Chat session endpoints — 401 without
    # a valid Bearer JWT, 403 for a non-admin caller.
    user = await require_admin(authorization)

    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON")
    if not isinstance(raw, dict):
        raise HTTPException(400, "Request body must be a JSON object")
    try:
        body = DeployShareBody(**raw)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise HTTPException(400, f"Invalid payload: {problems}")

    title = f"Deploy failed: {body.project} #{body.deploy_id}"
    try:
        session = await ora_session.create_session(user["user_id"], title)
    except RuntimeError:
        # create_session raises this when get_db() is None.
        logger.exception("deploy/share: database unavailable")
        raise HTTPException(503, "Database unavailable")
    # create_session truncates the title to 80 chars — return the
    # stored title so the response never disagrees with the inbox.
    stored_title = session["title"]
    ok = await ora_session.append_message(
        session["session_id"], user["user_id"],
        role="user", content=_build_message(body),
    )
    if not ok:
        logger.error(
            "deploy/share: session %s created but first message did "
            "not append (project=%s deploy_id=%s)",
            session["session_id"], body.project, body.deploy_id,
        )
        raise HTTPException(503, "Database unavailable")
    return {
        "thread_id": session["session_id"],
        "title": stored_title,
        "delivered": True,
    }
