"""
2026-09-20 — round 6: live-reproduced bug. Admin reported "CODEBASE
HEALTH SCORE / run coverage scan / timeout of 40000ms exceeded" on
the Admin Cockpit. Reproduced directly against the running app:
`POST /admin/health-score/test-coverage/run` had no server-side
concurrency guard — every call unconditionally launched a brand-new
pytest+coverage subprocess (real fork/exec CPU work) via
`asyncio.create_task`, and the frontend's only protection was a
client-side `disabled={running}` button state that resets on a page
reload or a second browser tab. Two overlapping subprocesses were
caught running simultaneously on this pod (`ps aux`), and
`GET /admin/health-score` latency degraded well beyond its normal
~4s baseline under that compounded CPU load — eventually tripping
the widget's 40s poll timeout.

Fix: an atomic Mongo-backed lock (`health_coverage_scan_lock`,
duplicate-key-upsert pattern with a stale-lock self-heal window) so
a second overlapping trigger is a fast, honest 409 no-op instead of
a second heavy subprocess.

Covers:
  - First call acquires the lock and proceeds to run.
  - A second call while the lock is held is rejected without
    launching a second subprocess.
  - The lock is released after the run completes, so a later call
    can proceed normally.
  - A stale lock (older than the timeout + margin) is treated as
    abandoned and can be re-acquired.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import health_coverage_scan as hcs


def _make_db():
    db = MagicMock()
    coll = MagicMock()
    coll.find_one_and_update = AsyncMock(return_value=None)
    coll.find_one = AsyncMock(return_value=None)
    coll.delete_one = AsyncMock()
    db.__getitem__ = MagicMock(return_value=coll)
    return db, coll


@pytest.mark.asyncio
async def test_second_concurrent_trigger_is_rejected_without_running_subprocess(monkeypatch):
    """The exact fix: a second `run_coverage_scan` call while the
    first is still "running" must never reach the subprocess code."""
    db, coll = _make_db()

    # First acquire succeeds (no existing lock doc).
    coll.find_one_and_update = AsyncMock(return_value=None)

    subprocess_calls = {"count": 0}

    async def _fake_do_run(_db):
        subprocess_calls["count"] += 1
        return {"ok": True}

    monkeypatch.setattr(hcs, "_do_run_coverage_scan", _fake_do_run)

    # Simulate: lock already held (DuplicateKeyError) for the SECOND call.
    from pymongo.errors import DuplicateKeyError
    call_n = {"n": 0}

    async def _acquire_side_effect(*_a, **_kw):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return None  # first call: acquire succeeds
        raise DuplicateKeyError("lock held")

    coll.find_one_and_update = AsyncMock(side_effect=_acquire_side_effect)

    result_1 = await hcs.run_coverage_scan(db)
    result_2 = await hcs.run_coverage_scan(db)

    assert subprocess_calls["count"] == 1, (
        "a second overlapping trigger must NOT run a second subprocess"
    )
    assert result_2["ok"] is False
    assert "already in progress" in result_2["error"]


@pytest.mark.asyncio
async def test_lock_released_after_run_allows_next_trigger(monkeypatch):
    db, coll = _make_db()
    coll.find_one_and_update = AsyncMock(return_value=None)  # always "acquired"

    async def _fake_do_run(_db):
        return {"ok": True}

    monkeypatch.setattr(hcs, "_do_run_coverage_scan", _fake_do_run)

    await hcs.run_coverage_scan(db)
    await hcs.run_coverage_scan(db)  # must not raise / must not be rejected

    assert coll.delete_one.await_count == 2, "lock must be released after every run"


@pytest.mark.asyncio
async def test_is_scan_in_progress_reflects_fresh_lock():
    db, coll = _make_db()
    coll.find_one = AsyncMock(return_value={"locked_at_ts": time.time()})
    assert await hcs.is_scan_in_progress(db) is True


@pytest.mark.asyncio
async def test_is_scan_in_progress_false_for_stale_lock():
    db, coll = _make_db()
    stale_ts = time.time() - (hcs.SUBPROCESS_TIMEOUT_S + 120)
    coll.find_one = AsyncMock(return_value={"locked_at_ts": stale_ts})
    assert await hcs.is_scan_in_progress(db) is False


@pytest.mark.asyncio
async def test_is_scan_in_progress_false_when_no_lock_doc():
    db, coll = _make_db()
    coll.find_one = AsyncMock(return_value=None)
    assert await hcs.is_scan_in_progress(db) is False
