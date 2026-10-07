"""
Iter 416 — POST /loop/{id}/cancel must report what it actually did.

BUG: `cancel_loop` swallowed failures from `release_loop_lock`,
`loop_events.insert_one` and the post-cancel force-clean at
`logger.debug` and then returned hardcoded `True` flags
(`lock_released`, `terminal_event_written`, `lock_force_released`).
A caller whose lock was NOT freed was told it was, and then hit
"loop_already_running" on the next /loop/start with nothing in the
response or the logs (at default level) to explain it.

These tests drive the real `cancel_loop` coroutine with stubbed
engine/DB collaborators and assert on the response body.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from routers import loop as loop_router
from services import loop_engine as eng
from services import loop_safety


class _Col:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.updates: list = []
        self.inserted: list = []
        self.deleted: list = []

    async def update_one(self, query, update):
        self.updates.append((query, update))

    async def insert_one(self, doc):
        if self.fail:
            raise RuntimeError("insert boom")
        self.inserted.append(doc)

    async def delete_many(self, query):
        if self.fail:
            raise RuntimeError("delete boom")
        self.deleted.append(query)


class _DB:
    def __init__(self, events_fail: bool = False, locks_fail: bool = False):
        self.loop_sessions = _Col()
        self.loop_events = _Col(events_fail)
        self.loop_locks = _Col(locks_fail)


def _wire(monkeypatch, db, *, engine=None, release=None):
    async def _dev(_auth):
        return {"user_id": "u1", "email": "u@example.com"}

    async def _no_rehydrate(_db, _loop_id):
        return None

    async def _load_session(_db, _loop_id):
        return {"user_id": "u1", "project_id": "p1", "phase": "ship"}

    monkeypatch.setattr(loop_router, "current_dev", _dev)
    monkeypatch.setattr(loop_router, "get_db", lambda: db)
    monkeypatch.setattr(eng, "lookup", lambda _loop_id: engine)
    monkeypatch.setattr(eng, "lookup_or_rehydrate", _no_rehydrate)
    monkeypatch.setattr(eng, "load_session", _load_session)
    if release is not None:
        monkeypatch.setattr(loop_safety, "release_loop_lock", release)


def _cancel() -> dict:
    return asyncio.run(loop_router.cancel_loop(
        loop_id="loop-1", authorization="Bearer x"))


def _live_engine():
    async def _cancel_noop():
        return None
    return SimpleNamespace(
        user_id="u1", project_id="p1", phase="ship",
        state=eng.LoopState.ABORTED, cancel=_cancel_noop)


def test_regression_iter416_fallback_reports_true_when_both_steps_succeed(monkeypatch):
    released: list = []

    async def _release(_db, proj, user, loop_id):
        released.append((proj, user, loop_id))

    db = _DB()
    _wire(monkeypatch, db, release=_release)
    out = _cancel()
    assert out["lock_released"] is True
    assert out["terminal_event_written"] is True
    assert released == [("p1", "u1", "loop-1")]
    assert len(db.loop_events.inserted) == 1


def test_regression_iter416_fallback_does_not_claim_lock_released_when_release_failed(monkeypatch):
    async def _release(*_a, **_k):
        raise RuntimeError("release boom")

    _wire(monkeypatch, _DB(), release=_release)
    out = _cancel()
    assert out["state"] == "aborted"
    assert out["lock_released"] is False
    assert out["terminal_event_written"] is True


def test_regression_iter416_fallback_does_not_claim_terminal_event_when_insert_failed(monkeypatch):
    async def _release(*_a, **_k):
        return None

    _wire(monkeypatch, _DB(events_fail=True), release=_release)
    out = _cancel()
    assert out["lock_released"] is True
    assert out["terminal_event_written"] is False


def test_regression_iter416_live_cancel_reports_force_release_when_it_happened(monkeypatch):
    db = _DB()
    _wire(monkeypatch, db, engine=_live_engine())
    out = _cancel()
    assert out["lock_force_released"] is True
    assert db.loop_locks.deleted == [
        {"project_id": "p1", "user_id": "u1", "loop_id": "loop-1"}]


def test_regression_iter416_live_cancel_does_not_claim_force_release_when_delete_failed(monkeypatch):
    _wire(monkeypatch, _DB(locks_fail=True), engine=_live_engine())
    out = _cancel()
    assert out["lock_force_released"] is False


def test_regression_iter416_live_cancel_does_not_claim_force_release_without_a_db(monkeypatch):
    _wire(monkeypatch, None, engine=_live_engine())
    out = _cancel()
    assert out["lock_force_released"] is False
