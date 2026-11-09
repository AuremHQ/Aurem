"""
routers/project_memory.py — ORA Brain Part 2, Phase B.

View/wipe endpoints for the per-project memory that
services/knowledge_injector.py::build_answer_context reads when
ORA_KNOWLEDGE_MODE=on. Both endpoints are scoped to the AUTHENTICATED
caller's own user_id (from current_dev) — project_id is read from the
query string but services/project_memory.py cross-checks it against
cto_projects ownership before any read/write/delete, so a project_id
belonging to another user is rejected rather than acted on.
"""
from __future__ import annotations

from fastapi import APIRouter, Header

from cto_services.db import require_db
from routers.auth import current_dev
from services.project_memory import delete_project_memory, get_project_memory

router = APIRouter(prefix="/api/project-memory", tags=["project-memory"])


@router.get("")
async def view_project_memory(
    project_id: str,
    authorization: str | None = Header(None),
) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    memory = await get_project_memory(db, me["user_id"], project_id)
    return {"project_id": project_id, "memory": memory}


@router.delete("")
async def wipe_project_memory(
    project_id: str,
    authorization: str | None = Header(None),
) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    await delete_project_memory(db, me["user_id"], project_id)
    return {"project_id": project_id, "deleted": True}
