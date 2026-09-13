"""
routers/architect.py — Phase 4 (Architect Agent ticket, admin "What I
set up" panel), approved 2026-09-13.

Single read-only endpoint. The panel reads DIRECTLY from
`project_blueprints` (via `services.architect_agent.get_blueprint`)
so it can never drift from what the Architect Agent actually decided
— no separate summarisation/caching layer, no re-deriving from chat
text. Resolution of pending Type B items is a deliberate follow-up
ticket (read-only for now, per the approved Phase 4 scope).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.architect_agent import get_blueprint

router = APIRouter(prefix="/architect", tags=["architect"])


@router.get("/blueprint/{project_id}")
async def get_project_blueprint(
    project_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """Powers the project settings 'What I set up' panel. Ownership-
    scoped the same way every other `/cto/projects/{id}/...` read
    endpoint is — only the project's own owner can read its
    blueprint. Returns `{"exists": false}` (not a 404) when the
    Architect Agent has never run for this project, since that's a
    normal, expected state (e.g. a project created before this
    feature, or one whose task was a scoped fix rather than a new
    build) rather than an error."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user["user_id"]},
        {"_id": 0, "project_id": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    doc = await get_blueprint(db, project_id=project_id)
    if not doc:
        return {"exists": False}
    return {"exists": True, **doc}
