"""
routers/brain_admin.py — ORA Brain Part 3/4.

Admin-only read endpoints over `brain_calls` (model scoreboard,
anonymized global-knowledge draft export, operational health) + a
user-facing delete-own-data endpoint covering everything the Brain
system itself owns. Same admin-gate shape as routers/advisor_context.
py's founder check (is_admin | tier=="founder" | founder email
allow-list).

Part 4, F3 — the ORIGINAL delete endpoint only removed brain_calls,
leaving project_memory behind, and lived under /api/admin/brain
despite being a plain user action, not an admin one. `delete_my_data`
below now removes BOTH brain_calls AND project_memory rows and is
exposed at the correct, non-admin path `/api/brain/my-data`; the
original `/api/admin/brain/my-data` path is kept as a byte-identical
alias so nothing that already calls it breaks.

Collections the Brain system does NOT own (and this endpoint
deliberately does NOT touch) — see the Part 4 report for the full
list and the explicit "founder decision" on what, if anything, to
do about them: ora_fix_learning, ora_scan_learning, ora_council_logs,
ora_learning_logs, ora_fabrication_incidents, ora_regression_patterns,
repo_contexts.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from cto_services.db import require_db
from routers.auth import current_dev
from services.brain_gateway import (
    delete_own_brain_calls,
    export_anonymized_global_draft,
    get_health_snapshot,
    get_model_scoreboard,
)
from services.project_memory import (
    delete_all_project_memory_for_user,
    delete_project_memory,
)
from services.usage import is_founder_email

admin_router = APIRouter(prefix="/api/admin/brain", tags=["brain-admin"])
user_router = APIRouter(prefix="/api/brain", tags=["brain-user"])
# Back-compat name some callers may already import as `router`.
router = admin_router


def _require_admin(me: dict) -> None:
    is_admin = bool(
        me.get("is_admin")
        or me.get("tier") == "founder"
        or is_founder_email(me.get("email"))
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin only")


@admin_router.get("/scoreboard")
async def model_scoreboard(authorization: str | None = Header(None)) -> dict:
    me = await current_dev(authorization)
    _require_admin(me)
    db = require_db()
    rows = await get_model_scoreboard(db)
    return {"scoreboard": rows}


@admin_router.get("/export-candidates")
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


@admin_router.get("/health")
async def brain_health(authorization: str | None = Header(None)) -> dict:
    """Part 4, F7 — admin-only operational visibility: write-queue
    size, whether the background drain worker is alive, how many
    rows have been dropped, volume/outcome-coverage over the last
    24h, how many rows have been promoted to candidate, and the
    current ORA_KNOWLEDGE_MODE value. Read-only — never changes
    anything."""
    me = await current_dev(authorization)
    _require_admin(me)
    db = require_db()
    snapshot = await get_health_snapshot(db)
    return snapshot


async def _delete_my_brain_data(user_id: str, project_id: str | None) -> dict:
    db = require_db()
    await delete_own_brain_calls(db, user_id, project_id)
    if project_id:
        await delete_project_memory(db, user_id, project_id)
    else:
        await delete_all_project_memory_for_user(db, user_id)
    return {"deleted": True, "project_id": project_id}


@user_router.delete("/my-data")
async def delete_my_data(
    project_id: str | None = None,
    authorization: str | None = Header(None),
) -> dict:
    """Part 4, F3 — deletes the caller's own brain_calls AND
    project_memory rows (optionally scoped to one project_id).
    Scoped entirely to the authenticated caller's own user_id —
    never request-supplied. This is the canonical path; the old
    /api/admin/brain/my-data path below is kept as an alias."""
    me = await current_dev(authorization)
    return await _delete_my_brain_data(me["user_id"], project_id)


@admin_router.delete("/my-data")
async def delete_my_brain_data(
    project_id: str | None = None,
    authorization: str | None = Header(None),
) -> dict:
    """Alias of DELETE /api/brain/my-data, kept for backward
    compatibility with anything that already calls this path. Same
    scoping, same behavior — not actually admin-only (any
    authenticated user may delete their OWN data), just historically
    mounted under /admin."""
    me = await current_dev(authorization)
    return await _delete_my_brain_data(me["user_id"], project_id)
