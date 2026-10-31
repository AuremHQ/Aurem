"""tests/test_regression_iter2026_28_brain_part4_f1_worker_bootstrap.py

ORA Brain Part 4, F1 — reviewer-proven bug: services/brain_gateway.py
start_worker() launched _drain_forever() without first ensuring
`_write_queue` existed. _drain_forever's first line was
`await _write_queue.get()` on the bare module global — if
start_worker() ran BEFORE any record_call() had ever created the
queue (exactly main.py's real boot order: the worker starts 38s
after boot, record_call may not fire before then), `_write_queue`
was None and the task died immediately on AttributeError, silently,
with nothing ever written until the next process restart.

This test drives the REAL background worker (asyncio.ensure_future
task), not drain_once_for_tests, and calls start_worker() BEFORE any
record_call() — the exact ordering that triggered the bug.
"""
from __future__ import annotations

import asyncio

import pytest

import services.brain_gateway as bg

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _reset_gateway_state(monkeypatch):
    monkeypatch.setattr(bg, "_write_queue", None)
    monkeypatch.setattr(bg, "_worker_task", None)
    monkeypatch.setattr(bg, "_dropped_count", 0)
    yield
    # start_worker() launches a detached, infinite `while True` task.
    # Leaving it running past this test (bound to a per-test event
    # loop that's about to be torn down) is exactly the kind of
    # orphaned-task situation that can wedge the test process at
    # interpreter shutdown — always cancel it explicitly.
    task = bg._worker_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestF1WorkerSurvivesStartBeforeAnyRecordCall:
    async def test_worker_task_does_not_die_when_started_before_queue_exists(self):
        assert bg._write_queue is None  # the exact precondition that triggered F1
        bg.start_worker()
        await asyncio.sleep(0.05)
        assert bg._worker_task is not None
        assert not bg._worker_task.done(), (
            f"worker died: {bg._worker_task.exception() if bg._worker_task.done() else None}"
        )

    async def test_start_worker_itself_creates_the_queue(self):
        assert bg._write_queue is None
        bg.start_worker()
        assert bg._write_queue is not None

    async def test_a_row_recorded_after_early_start_worker_is_actually_drained(self, monkeypatch):
        """End-to-end proof, not just 'task didn't crash': a row
        queued AFTER the early start_worker() call must actually
        reach Mongo via the real background task."""
        class _FakeColl:
            def __init__(self):
                self.rows = []

            async def insert_one(self, row):
                self.rows.append(row)

        class _FakeDB:
            def __getitem__(self, name):
                return self._coll

            def __init__(self):
                self._coll = _FakeColl()

        fake_db = _FakeDB()
        monkeypatch.setattr("cto_services.db.get_db", lambda: fake_db)

        bg.start_worker()  # before any record_call — the bug's precondition
        await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, surface="test",
        )
        for _ in range(50):
            if fake_db._coll.rows:
                break
            await asyncio.sleep(0.02)
        assert fake_db._coll.rows, "row never reached Mongo via the real worker"
        assert fake_db._coll.rows[0]["user_id"] == "u1"

    async def test_dropped_count_increments_on_queue_full_without_raising(self, monkeypatch):
        monkeypatch.setattr(bg, "_QUEUE_MAXSIZE", 1)
        q = asyncio.Queue(maxsize=1)
        monkeypatch.setattr(bg, "_write_queue", q)
        await q.put({"dummy": 1})
        before = bg.dropped_count()
        call_id = await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, surface="test",
        )
        assert call_id is None
        assert bg.dropped_count() == before + 1

    async def test_dropped_count_increments_on_write_failure(self, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("mongo down")
        monkeypatch.setattr(bg, "_write_one", _boom)
        before = bg.dropped_count()
        await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, surface="test",
        )
        await bg.drain_once_for_tests()
        assert bg.dropped_count() == before + 1

    async def test_record_call_restarts_a_dead_worker(self, monkeypatch):
        """If the worker task is dead for ANY reason, the very next
        record_call() must notice (task.done()) and restart it — no
        process restart required."""
        bg.start_worker()
        await asyncio.sleep(0.02)
        real_task = bg._worker_task
        real_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await real_task
        assert bg._worker_task.done()

        await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, surface="test",
        )
        assert bg._worker_task is not real_task
        assert not bg._worker_task.done()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
