"""
tests/test_regression_backlog_cron_2026_09.py — Section 15.7 spec.

"Feedback Loop... Add a scheduled job that scans for any
failure_reason hitting its 3rd occurrence and automatically opens a
flagged regression-backlog item — detection is automatic, a human
still approves the actual fix."
"""
from __future__ import annotations

import time

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services import self_bug
from services.regression_backlog_cron import (
    _TASK_CHAIN_RECURRENCE_THRESHOLD,
    scan_and_flag_recurring_failure_reasons,
)


@pytest.fixture
async def real_db():
    import os
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL/DB_NAME not configured")
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=3000)
    db = client[db_name]
    yield db
    client.close()


def _sig(failure_reason: str) -> str:
    return self_bug.signature("task_chain_recurrence", {"subject": failure_reason})


async def _cleanup(db, failure_reason: str):
    sig = _sig(failure_reason)
    await db.self_bug_learned.delete_many({"signature": sig})
    await db.ora_self_bugs.delete_many({"signature": sig})
    await db.task_chains.delete_many({"failure_reason": failure_reason})


@pytest.mark.asyncio
async def test_below_threshold_does_not_flag(real_db):
    reason = f"test_reason_below_{int(time.time() * 1000)}"
    await _cleanup(real_db, reason)
    try:
        now = time.time()
        for _ in range(_TASK_CHAIN_RECURRENCE_THRESHOLD - 1):
            await real_db.task_chains.insert_one({"failure_reason": reason, "ts": now})

        await scan_and_flag_recurring_failure_reasons(real_db)

        doc = await real_db.self_bug_learned.find_one({"signature": _sig(reason)})
        assert doc is None, "must not flag below the Rule-of-Three threshold"
    finally:
        await _cleanup(real_db, reason)


@pytest.mark.asyncio
async def test_third_occurrence_auto_flags_a_regression_backlog_item(real_db):
    """The exact spec wording: 'the 3rd time the same bug class shows
    up ... becomes a permanent automated regression test' — here,
    becomes an automatically-flagged, human-reviewable backlog item."""
    reason = f"test_reason_third_{int(time.time() * 1000)}"
    await _cleanup(real_db, reason)
    try:
        now = time.time()
        for _ in range(_TASK_CHAIN_RECURRENCE_THRESHOLD):
            await real_db.task_chains.insert_one({"failure_reason": reason, "ts": now})

        result = await scan_and_flag_recurring_failure_reasons(real_db)
        assert result["flagged"] >= 1

        doc = await real_db.self_bug_learned.find_one({"signature": _sig(reason)})
        assert doc is not None, "3rd occurrence must auto-create a flagged item"
        assert doc["times_seen"] == _TASK_CHAIN_RECURRENCE_THRESHOLD
        assert doc["proposed_fix_review_status"] == "pending"
        assert doc["status"] == "open"

        audit_row = await real_db.ora_self_bugs.find_one({"signature": _sig(reason)})
        assert audit_row is not None
        assert audit_row["type"] == "task_chain_recurrence"
        assert audit_row["source"] == "task_chain_recurrence_scan"
        assert reason in audit_row["evidence"]
    finally:
        await _cleanup(real_db, reason)


@pytest.mark.asyncio
async def test_rescan_is_idempotent_does_not_duplicate_or_reflag(real_db):
    """Detection is automatic and recurring (this cron runs on a
    schedule), but re-scanning the SAME already-flagged pattern must
    not spam duplicate audit rows or reset an already-pending review."""
    reason = f"test_reason_idempotent_{int(time.time() * 1000)}"
    await _cleanup(real_db, reason)
    try:
        now = time.time()
        for _ in range(_TASK_CHAIN_RECURRENCE_THRESHOLD):
            await real_db.task_chains.insert_one({"failure_reason": reason, "ts": now})

        first = await scan_and_flag_recurring_failure_reasons(real_db)
        assert first["flagged"] == 1

        second = await scan_and_flag_recurring_failure_reasons(real_db)
        assert second["flagged"] == 0, "already-flagged pattern must not re-flag on every scan"

        audit_rows = await real_db.ora_self_bugs.count_documents({"signature": _sig(reason)})
        assert audit_rows == 1, "must not create a duplicate audit row on rescan"
    finally:
        await _cleanup(real_db, reason)


@pytest.mark.asyncio
async def test_a_human_must_still_approve_nothing_auto_applies(real_db):
    """'a human still approves the actual fix' — the flagged item must
    land in review-pending state, never auto-resolved/auto-fixed."""
    reason = f"test_reason_human_gate_{int(time.time() * 1000)}"
    await _cleanup(real_db, reason)
    try:
        now = time.time()
        for _ in range(_TASK_CHAIN_RECURRENCE_THRESHOLD + 2):
            await real_db.task_chains.insert_one({"failure_reason": reason, "ts": now})

        await scan_and_flag_recurring_failure_reasons(real_db)

        doc = await real_db.self_bug_learned.find_one({"signature": _sig(reason)})
        assert doc["proposed_fix_review_status"] == "pending"
        assert doc["status"] == "open"
        assert doc.get("fixed_on") is None
        assert doc.get("fixed_by") is None
    finally:
        await _cleanup(real_db, reason)
