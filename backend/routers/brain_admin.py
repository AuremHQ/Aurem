"""
routers/brain_admin.py — ORA Brain Part 3, Phase E.

Admin-only read endpoints over `brain_calls` (model scoreboard,
anonymized global-knowledge draft export) + a user-facing delete-own-
data endpoint. Same admin-gate shape as routers/advisor_context.py's
founder check (is_admin | tier=="founder" | founder email allow-list).
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from cto_services.db import require_db
from routers.auth import current_dev
from services.brain_gateway import (
    delete_own_brain_calls,
    export_anonymized_global_draft,
    get_model_scoreboard,
)
from services.usage import is_founder_email

router = APIRouter(prefix="/api/admin/brain", tags=["brain-admin"])


def _require_admin(me: dict) -> None:
    is_admin = bool(
        me.get("is_admin")
        or me.get("tier") == "founder"
        or is_founder_email(me.get("email"))
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/scoreboard")
async def model_scoreboard(authorization: str | None = Header(None)) -> dict:
    me = await current_dev(authorization)
    _require_admin(me)
    db = require_db()
    rows = await get_model_scoreboard(db)
    return {"scoreboard": rows}


@router.get("/export-candidates")
async def export_candidates(authorization: str | None = Header(None)) -> dict:
    """Admin-only, ANONYMIZED draft of Tier-2 candidate rows for the
    founder to review before manually approving anything into
    backend/knowledge/. This endpoint never writes to that directory
    — it only returns the draft."""
    me = await current_dev(authorization)
    _require_admin(me)
    db = require_db()
    draft = await export_anonymized_global_draft(db)
    return {"draft": draft}


@router.delete("/my-data")
async def delete_my_brain_data(
    project_id: str | None = None,
    authorization: str | None = Header(None),
) -> dict:
    """User-facing delete-on-request for their own brain_calls rows
    (optionally scoped to one project_id). Scoped entirely to the
    authenticated caller's own user_id — never request-supplied."""
    me = await current_dev(authorization)
    db = require_db()
    await delete_own_brain_calls(db, me["user_id"], project_id)
    return {"deleted": True}
