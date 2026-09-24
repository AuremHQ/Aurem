"""
routers/verification.py — Trust & Verification round, user/admin-facing
endpoints for Enhancements 3, 4 and 5.

  POST /verification/tasks/{task_id}/report-issue   (Enhancement 3)
  POST /verification/tasks/{task_id}/re-verify       (Enhancement 3)
  GET  /verification/tasks/{task_id}                 (Enhancement 2 — level/history)
  GET  /admin/trust-calibration                      (Enhancement 4, admin-only)
  POST /verification/scope-confirm/{proposal_id}/cancel  (Enhancement 5)
  GET  /verification/scope-confirm/{proposal_id}          (Enhancement 5)

Enhancement 1's "Confirm and proceed" path does NOT need its own
endpoint here — the frontend resends the ORIGINAL prompt to the
existing POST /chat/stream with `scope_confirmed_proposal_id` set;
the gate inside routers/chat/worker.py validates and consumes it
in-line (see services/scope_confirmation.confirm_proposal).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from routers._admin_common import _require_admin

router = APIRouter(tags=["Verification"])
logger = logging.getLogger(__name__)


class ReportIssueBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


@router.post("/verification/tasks/{task_id}/report-issue")
async def report_task_issue(
    task_id: str, body: ReportIssueBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Enhancement 3 — explicit user-reported retroactive downgrade.
    A previously-`done` task the CURRENT user owns is flipped to
    `needs_review`; the original success/verification record is
    preserved, never erased."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    task = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": user["user_id"]}, {"_id": 0, "status": 1},
    )
    if not task:
        raise HTTPException(404, "Task not found")
    from services.task_downgrade import downgrade_task
    result = await downgrade_task(
        db, task_id=task_id, reason=body.reason, source="user_reported",
    )
    if not result.get("ok"):
        raise HTTPException(409, "Could not downgrade this task")
    return result


@router.post("/verification/tasks/{task_id}/re-verify")
async def re_verify_task_endpoint(
    task_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """Enhancement 3 — a `needs_review` task returns to `done` ONLY
    through a brand-new qualifying verification run against current
    repo state, never a manual status flip."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    from services.task_downgrade import re_verify_task
    result = await re_verify_task(db, task_id=task_id, user_id=user["user_id"])
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "re_verify_failed"))
    return result


@router.get("/verification/tasks/{task_id}")
async def get_task_verification(
    task_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """Enhancement 2 — user-visible verification level + full
    append-only decision history for one task, scoped to the owner."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    task = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": user["user_id"]},
        {"_id": 0, "task_id": 1, "status": 1, "verification_status": 1,
         "verification_level": 1, "needs_review_reason": 1},
    )
    if not task:
        raise HTTPException(404, "Task not found")
    from services import verification as _v
    history = await _v.get_verification_history(db, task_id)
    return {
        "ok": True,
        "task_id": task_id,
        "status": task.get("status"),
        "verification_status": task.get("verification_status"),
        "verification_level": task.get("verification_level"),
        "verification_level_display": _v.display_level(task.get("verification_level")),
        "needs_review_reason": task.get("needs_review_reason"),
        "history": history,
    }


@router.get("/admin/trust-calibration")
async def get_trust_calibration(authorization: Optional[str] = Header(None)) -> dict:
    """Enhancement 4 — authorized internal-dashboard panel. Admin-only
    (this exposes sanitized task_ids across the whole platform, not
    scoped to one user)."""
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    from services.trust_calibration import get_latest_snapshot, compute_correction_rate_30d
    snap = await get_latest_snapshot(db)
    if snap is None:
        # No cron tick has run yet — compute on demand rather than
        # showing nothing (never fabricate a fake 0%; still correctly
        # reports "not enough data" when the ledger is empty).
        snap = await compute_correction_rate_30d(db)
    return {"ok": True, "snapshot": snap}


@router.post("/verification/scope-confirm/{proposal_id}/cancel")
async def cancel_scope_confirmation(
    proposal_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """Enhancement 5 — explicit Cancel. Never executes."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    from services.scope_confirmation import cancel_proposal
    return await cancel_proposal(db, proposal_id=proposal_id, user_id=user["user_id"])


@router.get("/verification/scope-confirm/{proposal_id}")
async def get_scope_confirmation(
    proposal_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """Lets the frontend poll a proposal's live status (pending /
    confirmed / cancelled / expired) after a reconnect."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    from services.scope_confirmation import _get_live
    doc = await _get_live(db, proposal_id, user["user_id"])
    if not doc:
        raise HTTPException(404, "Proposal not found")
    return {"ok": True, "proposal": doc}
