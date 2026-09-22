"""
services/regression_backlog_cron.py — 2026-09-22, Section 15.7 spec.

"Feedback Loop — the piece that makes this compound instead of
repeat." Confirmed missing in the System Audit v2: failures reach the
admin Failure Patterns dashboard (routers/admin_analytics.py::failure_
patterns()) and stop there — nothing automatically feeds a recurring
pattern back into a backlog. This closes that gap by reusing the
EXISTING self-repair review pipeline (services/self_bug.py +
routers/self_bugs_admin.py), not a new parallel system:

  1. Scan `task_chains` for any `failure_reason` that has occurred
     `_TASK_CHAIN_RECURRENCE_THRESHOLD` (3) or more times in the
     lookback window.
  2. For each one crossing the threshold, upsert a
     `self_bug_learned` row (type="task_chain_recurrence") with
     `times_seen` reconciled to the REAL count (via Mongo's `$max`,
     never decreasing, never artificially inflated by re-scanning) —
     then let the SAME `self_bug.propose_fix()` logic used by every
     other self-bug type decide whether to attach a proposed-fix
     text.
  3. Detection is fully automatic. Nothing auto-applies a fix — a
     human still reviews/approves via the existing
     `POST /admin/self-bugs/review-proposal` endpoint, same standard
     as every other self-bug type in this codebase.

Same recurring-cron pattern as services/restore_drill_cron.py and
services/rollback_drill_cron.py — daily by default, no manual trigger
required for detection (a manual trigger is provided for tests/ops).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time as _time

from pymongo import ReturnDocument

from services import self_bug

logger = logging.getLogger("regression_backlog_cron")

SCAN_INTERVAL_SECONDS = int(os.environ.get("REGRESSION_BACKLOG_SCAN_INTERVAL_SECONDS", str(86400)))
STARTUP_DELAY_SECONDS = int(os.environ.get("REGRESSION_BACKLOG_STARTUP_DELAY_SECONDS", "300"))
LOOKBACK_DAYS = int(os.environ.get("REGRESSION_BACKLOG_LOOKBACK_DAYS", "7"))

# "Rule of Three" — the exact spec wording. Kept as its own constant
# here (not just relying on self_bug's per-type override) so this
# scan's own behaviour is self-documenting independent of self_bug.py.
_TASK_CHAIN_RECURRENCE_THRESHOLD = 3


async def scan_and_flag_recurring_failure_reasons(db, lookback_days: int = LOOKBACK_DAYS) -> dict:
    """One scan pass. Returns {"scanned": N, "flagged": N} — never
    raises (a flagging-pipeline failure must never take down the
    process it's monitoring)."""
    if db is None:
        return {"scanned": 0, "flagged": 0, "skipped": "no_db"}
    try:
        cutoff = _time.time() - lookback_days * 86400
        pipeline = [
            {"$match": {"ts": {"$gte": cutoff}, "failure_reason": {"$ne": None}}},
            {"$group": {"_id": "$failure_reason", "count": {"$sum": 1}}},
        ]
        rows = await db.task_chains.aggregate(pipeline).to_list(None)
        from services.interaction_contract import enabled
        if enabled():
            # Count distinct turns, not retries within a turn; never infer failure
            # from the final prose. Includes recovered turns and calls after #10.
            tool_rows = await db.task_chains.aggregate([
                {"$match": {"ts": {"$gte": cutoff}, "tool_failure_signatures.0": {"$exists": True}}},
                {"$unwind": "$tool_failure_signatures"},
                {"$group": {"_id": {"signature": "$tool_failure_signatures", "turn_id": "$turn_id"}}},
                {"$group": {"_id": "$_id.signature", "count": {"$sum": 1}}},
            ]).to_list(None)
            rows.extend(tool_rows)
    except Exception as e:
        logger.warning("regression_backlog scan: task_chains aggregate failed: %r", e)
        return {"scanned": 0, "flagged": 0, "error": str(e)}

    flagged = 0
    for row in rows:
        reason = row.get("_id")
        count = row.get("count", 0)
        if not reason or count < _TASK_CHAIN_RECURRENCE_THRESHOLD:
            continue
        try:
            newly_flagged = await _reconcile_one(db, reason, count)
            if newly_flagged:
                flagged += 1
        except Exception as e:
            logger.warning("regression_backlog scan: reconcile failed for %r: %r", reason, e)

    return {"scanned": len(rows), "flagged": flagged}


async def _reconcile_one(db, failure_reason: str, real_count: int) -> bool:
    """Idempotent — safe to call every scan cycle forever. Returns
    True only the FIRST time this signature crosses the threshold and
    gets a proposal attached (so callers/tests can tell "newly
    flagged" from "already flagged, just reconciled the count")."""
    sig = self_bug.signature("task_chain_recurrence", {"subject": failure_reason})
    doc = await db.self_bug_learned.find_one_and_update(
        {"signature": sig},
        {
            "$max": {"times_seen": real_count},
            "$set": {"last_seen": _time.time()},
            "$setOnInsert": {
                "first_seen": _time.time(), "status": "open",
                "fixed_on": None, "fixed_by": None,
            },
        },
        upsert=True, return_document=ReturnDocument.AFTER, projection={"_id": 0},
    )
    times_seen = (doc or {}).get("times_seen", real_count)
    status = (doc or {}).get("status", "open")
    already_pending = (doc or {}).get("proposed_fix_review_status") == "pending"

    proposal = self_bug.propose_fix("task_chain_recurrence", times_seen, status)
    if not proposal or already_pending:
        return False

    await db.self_bug_learned.update_one(
        {"signature": sig},
        {"$set": {
            "proposed_fix_text": proposal,
            "proposed_fix_generated_at": _time.time(),
            "proposed_fix_review_status": "pending",
        }},
    )
    # Visible in the SAME /admin/self-bugs/list feed as every other
    # self-bug type — this scan doesn't get its own separate page.
    await db.ora_self_bugs.insert_one({
        "type": "task_chain_recurrence",
        "source": "task_chain_recurrence_scan",
        "evidence": f"failure_reason={failure_reason!r} occurred {real_count}x "
                    f"in the last {LOOKBACK_DAYS} days (Failure Patterns telemetry)",
        "context": {"subject": failure_reason},
        "what_user_saw": f"a recurring pattern: {failure_reason}",
        "what_ora_detected": "task_chain_recurrence_scan flagged a repeating failure_reason",
        "likely_cause": self_bug._KNOWN_CAUSES["task_chain_recurrence"],
        "confidence": "confirmed",
        "severity": "high" if real_count >= 10 else "low",
        "proposed_fix": proposal,
        "signature": sig,
        "ts": _time.time(),
    })
    logger.info("regression_backlog: flagged new recurring failure_reason=%r (%dx)", failure_reason, real_count)
    return True


async def regression_backlog_cron(db_getter=None) -> None:
    try:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
    except asyncio.CancelledError:
        raise
    while True:
        try:
            db = db_getter() if db_getter else None
            if db is None:
                logger.warning("regression_backlog_cron: no db available — skipping this cycle")
            else:
                result = await scan_and_flag_recurring_failure_reasons(db)
                if result.get("flagged"):
                    logger.info("regression_backlog_cron: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("regression_backlog_cron loop error: %r", e)
        try:
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


__all__ = [
    "SCAN_INTERVAL_SECONDS",
    "_TASK_CHAIN_RECURRENCE_THRESHOLD",
    "regression_backlog_cron",
    "scan_and_flag_recurring_failure_reasons",
]
