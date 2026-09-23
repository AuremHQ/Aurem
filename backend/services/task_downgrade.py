"""
services/task_downgrade.py — Trust & Verification round, Enhancement 3
(Retroactive Downgrade).

A previously-accepted `done` task can be revoked to `needs_review`
when relevant evidence later proves it invalid — WITHOUT losing the
original success/verification record (append-only history in
services/verification.py, never erased) and WITHOUT allowing a
downgraded task back to success except through a brand-NEW qualifying
verification (never a manual status flip, never a replay of the old,
already-invalidated verdict).

Trigger sources supported this round:
  - "user_reported"          — POST /verification/tasks/{id}/report-issue
  - "user_reported_implicit" — the next chat message in the same
    session reads as a correction report ("still broken", "that
    didn't work") shortly after a task shipped in that session (see
    `maybe_auto_downgrade_recent_task`, called from routers/chat/worker.py).
  - "re_verification_failed" — a later independent-verifier re-run
    against current repo state returns "fail" (see `re_verify_task`).

Atomicity: the status transition is a CONDITIONAL update
(`{"task_id": id, "status": "done"}` -> `needs_review`), so a
duplicate/late trigger can never double-downgrade, double-notify, or
double-count on the trust-calibration metric — `modified_count == 0`
means "already handled", a safe no-op.
"""
from __future__ import annotations

import logging
import re as _re
import time
from typing import Optional

from services import verification as _v

logger = logging.getLogger(__name__)

RECENT_TASK_WINDOW_S = 24 * 60 * 60  # 24h — same-session correction report

_NEGATIVE_CORRECTION_RE = _re.compile(
    r"\b(still (broken|failing|not working)|didn'?t work|doesn'?t work|"
    r"not (actually )?fixed|that'?s not fixed|still (an? )?(issue|bug|problem)|"
    r"you broke|this broke|regressed|it'?s worse|made it worse)\b",
    _re.IGNORECASE,
)


def looks_like_correction_report(message: str) -> bool:
    return bool(_NEGATIVE_CORRECTION_RE.search(message or ""))


async def downgrade_task(
    db, *, task_id: str, reason: str, source: str,
    evidence_ref: Optional[str] = None,
) -> dict:
    """Returns {ok, downgraded, already, task}. `downgraded=False,
    already=True` on a duplicate/late event — never a duplicate
    notification or metric increment."""
    if db is None or not task_id:
        return {"ok": False, "downgraded": False, "already": False, "task": None}

    task = await db.cto_tasks.find_one({"task_id": task_id}, {"_id": 0})
    if not task:
        return {"ok": False, "downgraded": False, "already": False, "task": None}

    now = time.time()
    res = await db.cto_tasks.update_one(
        {"task_id": task_id, "status": "done"},
        {"$set": {
            "status": "needs_review",
            "needs_review_reason": (reason or "")[:500],
            "needs_review_source": source,
            "needs_review_at": now,
        }},
    )
    if res.modified_count == 0:
        # Either already needs_review (duplicate trigger) or never
        # was "done" in the first place — both are safe no-ops.
        return {"ok": True, "downgraded": False, "already": True, "task": task}

    revision_id = _v.compute_task_revision_id(
        task.get("result") or task.get("task") or "", task.get("edits") or {},
    )
    # Append-only invalidation event — the original PASS verification
    # row above it in the ledger is never touched or deleted.
    await _v.record_verification(
        db, task_id=task_id, project_id=task.get("project_id"),
        user_id=task.get("user_id"), task_revision_id=revision_id,
        verified_by=f"downgrade:{source}",
        executed_by=task.get("agent_used") or "unknown",
        decision=_v.DECISION_DOWNGRADED, reason=reason, origin="downgrade",
        evidence_refs=[evidence_ref] if evidence_ref else [],
    )

    try:
        from services.cto_projects_helpers import _emit
        await _emit(task_id, "needs_review", kind="needs_review",
                   reason=reason, source=source)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("downgrade_task: SSE emit failed: %r", e)

    try:
        from services.notifications import emit_notification
        await emit_notification(
            db, user_id=task.get("user_id"), type="task_needs_review",
            text=(f"A previously-shipped fix needs review: {reason}")[:280],
            project_id=task.get("project_id"),
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("downgrade_task: notification failed: %r", e)

    updated = await db.cto_tasks.find_one({"task_id": task_id}, {"_id": 0})
    return {"ok": True, "downgraded": True, "already": False, "task": updated}


async def re_verify_task(db, *, task_id: str, user_id: str) -> dict:
    """A `needs_review` task returns to `done` ONLY through a NEW
    qualifying verification — never a manual status flip, never a
    replay of the old (already-invalidated) verdict."""
    if db is None:
        return {"ok": False, "error": "no_db"}
    task = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": user_id}, {"_id": 0},
    )
    if not task:
        return {"ok": False, "error": "task_not_found"}
    if task.get("status") != "needs_review":
        return {"ok": False, "error": "task_not_in_needs_review"}

    from services.task_independent_verifier import verify_task_completion
    result = await verify_task_completion(
        db, task_id=task_id, project_id=task.get("project_id"),
        user_id=user_id, task_text=task.get("result") or task.get("task") or "",
        files=task.get("edits") or {},
        executed_by=task.get("agent_used") or "unknown",
        origin="re_verification",
    )
    if result.get("decision") != _v.DECISION_PASS:
        return {"ok": True, "restored": False, "decision": result.get("decision"),
                "reason": result.get("reason")}

    now = time.time()
    res = await db.cto_tasks.update_one(
        {"task_id": task_id, "status": "needs_review"},
        {"$set": {
            "status": "done", "re_verified_at": now,
            "verification_status": _v.DECISION_PASS,
            "verification_level": result.get("verification_level"),
        }, "$unset": {"needs_review_reason": "", "needs_review_source": ""}},
    )
    if res.modified_count == 0:
        return {"ok": True, "restored": False, "reason": "concurrent_state_change"}
    try:
        from services.cto_projects_helpers import _emit
        await _emit(task_id, "restored", kind="restored")
    except Exception:                                          # noqa: BLE001
        pass
    updated = await db.cto_tasks.find_one({"task_id": task_id}, {"_id": 0})
    return {"ok": True, "restored": True, "task": updated}


async def maybe_auto_downgrade_recent_task(
    db, *, session_id: str, user_id: str, message: str,
) -> Optional[dict]:
    """Best-effort, never raises. If the user's new message reads as
    a correction report AND the most recent chat turn in this session
    that shipped a task ('turns[].shipped_task_id') is still `done`
    and recent, downgrade it. Returns the downgrade result dict, or
    None if nothing matched — callers must not treat None as an
    error."""
    if db is None or not session_id or not looks_like_correction_report(message):
        return None
    try:
        sess = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "turns": 1},
        )
        turns = (sess or {}).get("turns") or []
        cutoff = time.time() - RECENT_TASK_WINDOW_S
        recent_task_id = None
        for t in reversed(turns):
            tid = (t or {}).get("shipped_task_id")
            if not tid:
                continue
            ts = (t or {}).get("ts") or 0
            if ts and ts < cutoff:
                break  # turns are chronological — nothing newer qualifies either
            recent_task_id = tid
            break
        if not recent_task_id:
            return None
        task = await db.cto_tasks.find_one(
            {"task_id": recent_task_id, "user_id": user_id, "status": "done"},
            {"_id": 0},
        )
        if not task:
            return None
        return await downgrade_task(
            db, task_id=recent_task_id,
            reason=f"user follow-up reads as a correction report: {message[:150]!r}",
            source="user_reported_implicit",
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("maybe_auto_downgrade_recent_task failed: %r", e)
        return None
