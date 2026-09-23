"""
services/trust_calibration.py — Trust & Verification round, Enhancement
4 (Trust-Calibration Metric).

Measures how often ACCEPTED success later required correction — NOT a
lifetime reliability guarantee, and explicitly NOT the source of any
"X% reliable" marketing claim (see scripts/lint_absolute_claims.py,
Enhancement 6, for the guard against that exact failure mode).

    correction_rate_30d =
        distinct cohort revisions with a qualifying "downgraded" event
        -----------------------------------------------------------------
        distinct cohort revisions with a first accepted `pass` in-window
        × 100

Cohort definition: distinct `task_revision_id` values whose EARLIEST
accepted `pass` row in `task_verifications` (services/verification.py)
fell within the rolling previous 30 days, evaluated in UTC. A revision
counts at most ONCE in numerator and denominator no matter how many
times it was later downgraded/re-verified; re-verification does NOT
reset the revision's original cohort timestamp (re-verification writes
a fresh `pass` row, but under the SAME task_revision_id it already had
— its cohort membership was fixed the first time that revision passed).
Only a "downgraded" event counts toward the numerator — a pre-ship
"fail" verdict never became accepted success, so it is not a
correction of one. Zero denominator -> "Not enough data", never "0%".
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from services import verification as _v

logger = logging.getLogger(__name__)

SNAPSHOT_COLLECTION = "trust_calibration_snapshots"
WINDOW_DAYS = 30
FORMULA_VERSION = "v1"


async def compute_correction_rate_30d(db) -> dict:
    """Pure computation off the append-only `task_verifications`
    ledger — idempotent, safe to re-run any number of times; the
    result for a given window is deterministic because the ledger it
    reads is immutable historical data."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)
    window_start_ts = window_start.timestamp()
    now_ts = now.timestamp()

    first_pass_at: dict = {}
    if db is not None:
        cursor = db[_v.COLLECTION].find(
            {"decision": _v.DECISION_PASS},
            {"_id": 0, "task_revision_id": 1, "created_at": 1},
        )
        async for row in cursor:
            rid = row.get("task_revision_id")
            if not rid:
                continue
            ts = row.get("created_at") or 0
            if rid not in first_pass_at or ts < first_pass_at[rid]:
                first_pass_at[rid] = ts

    cohort = {
        rid for rid, ts in first_pass_at.items()
        if window_start_ts <= ts <= now_ts
    }
    denominator = len(cohort)

    numerator = 0
    if cohort and db is not None:
        corrected = set()
        cursor = db[_v.COLLECTION].find(
            {"decision": _v.DECISION_DOWNGRADED,
             "task_revision_id": {"$in": list(cohort)}},
            {"_id": 0, "task_revision_id": 1},
        )
        async for row in cursor:
            corrected.add(row.get("task_revision_id"))
        numerator = len(corrected)

    rate = None if denominator == 0 else round(numerator / denominator * 100, 2)
    return {
        "window_days":         WINDOW_DAYS,
        "window_start":        window_start.isoformat(),
        "window_end":          now.isoformat(),
        "numerator":           numerator,
        "denominator":         denominator,
        "correction_rate_pct": rate,
        "not_enough_data":     denominator == 0,
        "formula_version":     FORMULA_VERSION,
        "computed_at":         now.isoformat(),
        "snapshot_date":       now.date().isoformat(),
    }


async def save_snapshot(db, snapshot: dict) -> None:
    """Idempotent upsert keyed by `snapshot_date` — recomputation on
    the same UTC day overwrites that day's row rather than
    accumulating duplicates; a missed cron tick just means the NEXT
    tick recomputes and upserts the correct day, with no lost state."""
    if db is None:
        return
    try:
        await db[SNAPSHOT_COLLECTION].update_one(
            {"snapshot_date": snapshot["snapshot_date"]},
            {"$set": snapshot},
            upsert=True,
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("trust_calibration.save_snapshot failed: %r", e)


async def get_latest_snapshot(db) -> Optional[dict]:
    if db is None:
        return None
    return await db[SNAPSHOT_COLLECTION].find_one(
        {}, {"_id": 0}, sort=[("computed_at", -1)],
    )


async def ensure_indexes(db) -> None:
    if db is None:
        return
    await db[SNAPSHOT_COLLECTION].create_index("snapshot_date", unique=True)
