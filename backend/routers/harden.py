"""
aurem_cto.routers.harden — Server hardening status (read-only).

Surfaces the most recent hardening report for the authenticated user.
The actual hardening run (SSH in, apt update/upgrade, install Docker +
Caddy, lock down root SSH, create deploy user) is a manual operation
right now — request it via auremcto.com/support. This endpoint just
serves the persisted report once that work is done.
"""
from __future__ import annotations

from fastapi import APIRouter, Header

from cto_services.auth import current_dev
from cto_services.db import require_db
from services.team_permissions import check_permission

router = APIRouter(prefix="/harden", tags=["AUREM Hardening"])


@router.get("/last")
async def last_report(authorization: str = Header(None)) -> dict:
    """Returns the most recent hardening report for this account, or null."""
    me = await current_dev(authorization)
    # 2026-09-26 P0 RBAC fix (Task 2).
    db = require_db()
    ctx = await check_permission(me, "projects", "read", db=db)
    row = await db.aurem_cto_server_hardenings.find_one(
        {"user_id": ctx["effective_account_id"]},
        {"_id": 0},
        sort=[("ts", -1)],
    )
    return {"report": row}
