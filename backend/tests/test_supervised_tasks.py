"""Session F — Supervised background-tasks smoke test.

Zero-mocks: uses a real asyncio loop and a real in-memory dict as a
Guard 20 stand-in (via `db_getter`). The incident_log integration is
proven separately in `tests/test_iter363_guard20_incident_log.py` —
here we prove the SUPERVISOR wiring: kill a supervised task, confirm
detection + postmortem + incident record write.
"""
from __future__ import annotations

import asyncio

import pytest

from services import supervised_tasks


@pytest.fixture(autouse=True)
def _reset_registry():
    """Wipe supervisor state between tests so registry entries from
    one test can't leak into another."""
    supervised_tasks._reset_for_tests()
    yield
    supervised_tasks._reset_for_tests()


class _FakeIncidents:
    """Tiny stand-in for `db.incidents` — records every insert so the
    test can assert the incident row was actually written."""
    def __init__(self):
        self.rows: list[dict] = []

    async def find_one(self, *_a, **_kw):
        return None

    async def find_one_and_update(self, filter, update, *, upsert=False,
                                  return_document=None, projection=None):
        """Emulate the atomic upsert `open_incident()` uses (Aug 25
        TOCTOU fix): the first call for a (source_key, status=open)
        pair creates the row from the pipeline's $ifNull defaults;
        later calls bump recurrence and refresh last_seen. Only the
        one-stage $set pipeline this caller uses is interpreted —
        anything else raises so a silent semantic drift can't hide
        here. Returns the AFTER document (what open_incident asks
        for); BEFORE returns the pre-update copy."""
        from pymongo import ReturnDocument
        row = next((r for r in self.rows
                    if r.get("source_key") == filter.get("source_key")
                    and r.get("status") == "open"), None)
        if row is None:
            if not upsert:
                return None
            row = {}
            self.rows.append(row)
        before = dict(row)
        stages = update if isinstance(update, list) else [update]
        for stage in stages:
            sets = stage.get("$set", {})
            if not isinstance(sets, dict):
                raise AssertionError(f"fake can't emulate stage: {stage!r}")
            for key, expr in sets.items():
                if isinstance(expr, dict) and "$ifNull" in expr:
                    _field, default = expr["$ifNull"]
                    row.setdefault(key, default)
                elif isinstance(expr, dict) and "$add" in expr:
                    base, inc = expr["$add"]
                    if isinstance(base, dict) and "$ifNull" in base:
                        _f, default = base["$ifNull"]
                        row[key] = row.get(key, default) + inc
                    else:
                        row[key] = row.get(key, 0) + inc
                else:
                    row[key] = expr
        if return_document is not None and return_document != ReturnDocument.AFTER:
            return before
        return dict(row)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def update_one(self, *_a, **_kw):
        return None


class _FakeDB:
    def __init__(self):
        self.incidents = _FakeIncidents()


@pytest.mark.asyncio
async def test_supervised_cron_exception_records_incident():
    """A supervised long-lived cron that raises must:
      1. Land in `_DEAD` with reason='exception'
      2. Write a Guard 20 incident row via open_incident()
    """
    db = _FakeDB()

    async def _broken_cron():
        await asyncio.sleep(0.01)
        raise RuntimeError("simulated cron blow-up")

    task = supervised_tasks.supervise(
        _broken_cron(),
        name="test_broken_cron",
        db_getter=lambda: db,
        long_lived=True,
    )
    # Wait for the coroutine to raise + the done-callback + the
    # scheduled incident-write to complete.
    with pytest.raises(RuntimeError, match="simulated cron blow-up"):
        await task
    # Let the fire-and-forget incident recorder finish.
    await asyncio.sleep(0.05)

    dead = supervised_tasks._DEAD
    assert "test_broken_cron" in dead, f"expected death record, got {dead!r}"
    assert dead["test_broken_cron"]["reason"] == "exception"
    assert dead["test_broken_cron"]["exc_type"] == "RuntimeError"
    assert "simulated cron blow-up" in dead["test_broken_cron"]["exc_msg"]

    # Guard 20 incident must have been written.
    assert len(db.incidents.rows) == 1, (
        f"expected 1 incident row, got {db.incidents.rows!r}"
    )
    row = db.incidents.rows[0]
    assert row["guard"] == "G-F1-supervised-task"
    assert row["severity"] == "critical"
    assert row["source_key"] == "supervised_task:test_broken_cron"
    assert "test_broken_cron" in row["title"]


@pytest.mark.asyncio
async def test_supervised_cron_silent_completion_records_incident():
    """A supervised LONG-LIVED cron that returns normally without an
    exception is treated as unexpected death — crons must loop forever.
    """
    db = _FakeDB()

    async def _premature_cron():
        # Simulates a cron that exits its outer while-loop for any
        # reason (unhandled break, misplaced return, etc.) — this is
        # the exact silent-death bug supervision is meant to catch.
        await asyncio.sleep(0.01)
        return

    task = supervised_tasks.supervise(
        _premature_cron(),
        name="test_premature_cron",
        db_getter=lambda: db,
        long_lived=True,
    )
    await task
    await asyncio.sleep(0.05)

    dead = supervised_tasks._DEAD
    assert "test_premature_cron" in dead
    assert dead["test_premature_cron"]["reason"] == "silent_completion"

    assert len(db.incidents.rows) == 1
    assert db.incidents.rows[0]["source_key"] == (
        "supervised_task:test_premature_cron"
    )


@pytest.mark.asyncio
async def test_supervised_one_shot_normal_return_is_not_incident():
    """A one-shot startup task (backfill, index-create) that returns
    normally is EXPECTED behaviour — must not write an incident."""
    db = _FakeDB()

    async def _one_shot_backfill():
        await asyncio.sleep(0.01)
        return

    task = supervised_tasks.supervise(
        _one_shot_backfill(),
        name="test_one_shot",
        db_getter=lambda: db,
        long_lived=False,
    )
    await task
    await asyncio.sleep(0.05)

    assert "test_one_shot" not in supervised_tasks._DEAD
    assert len(db.incidents.rows) == 0


@pytest.mark.asyncio
async def test_supervised_cancel_is_silent():
    """Explicit cancellation (pod shutdown) must NOT open an incident."""
    db = _FakeDB()

    async def _long_sleeper():
        await asyncio.sleep(1000)  # will be cancelled before completing

    task = supervised_tasks.supervise(
        _long_sleeper(),
        name="test_cancelled",
        db_getter=lambda: db,
        long_lived=True,
    )
    # Give the coroutine one loop tick, then cancel.
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)

    assert "test_cancelled" not in supervised_tasks._DEAD
    assert len(db.incidents.rows) == 0


@pytest.mark.asyncio
async def test_health_snapshot_shape():
    """`health_snapshot()` must be safe to embed in /api/health."""
    db = _FakeDB()

    async def _long_alive():
        await asyncio.sleep(0.05)

    supervised_tasks.supervise(
        _long_alive(),
        name="alive_task_a",
        db_getter=lambda: db,
        long_lived=False,
    )

    snap = supervised_tasks.health_snapshot()
    assert snap["supervised_count"] == 1
    assert "alive_task_a" in snap["alive"]
    assert snap["dead"] == []

    # Let it finish so we don't leak a running task past the test.
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_supervise_is_idempotent_on_same_name():
    """A second supervise(...) with the same name while the first
    task is still alive must return the SAME task (no duplicate)."""
    async def _sleeper():
        await asyncio.sleep(0.1)

    t1 = supervised_tasks.supervise(_sleeper(), name="dup", long_lived=True)
    dup_coro = _sleeper()
    try:
        t2 = supervised_tasks.supervise(dup_coro, name="dup", long_lived=True)
        assert t1 is t2, "supervise must be idempotent for still-alive tasks"
    finally:
        # The dedupe path drops `dup_coro` on the floor — close it
        # explicitly so pytest doesn't emit "coroutine was never
        # awaited" ResourceWarning.
        dup_coro.close()

    await t1


# ── Iter 363 gap fix (2026-11-21): resolve_recovered_tasks ──────────
# A supervised cron dying opens an incident, but nothing ever resolved
# it — not even a pod restart that brought the cron back. The sweeper
# must close the incident ONLY when the task is alive again, and leave
# every other guard's incidents alone.
class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, _key, _direction):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n):
        return [dict(d) for d in self._docs[:n]]


def _match_eq(doc, query):
    for k, cond in query.items():
        if isinstance(cond, dict):
            if "$ne" in cond and doc.get(k) == cond["$ne"]:
                return False
            if "$gte" in cond and not (
                doc.get(k) is not None and doc.get(k) >= cond["$gte"]
            ):
                return False
        elif doc.get(k) != cond:
            return False
    return True


class _FakeIncidentColl:
    """Minimal incidents collection: the exact calls `list_incidents`
    + `resolve_incident` make (find / find_one / update_one)."""
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query, proj=None):
        return _FakeCursor([r for r in self.rows if _match_eq(r, query)])

    async def find_one(self, query, proj=None):
        for r in self.rows:
            if _match_eq(r, query):
                return dict(r)
        return None

    async def update_one(self, query, update):
        for r in self.rows:
            if _match_eq(r, query):
                for k, v in update.get("$set", {}).items():
                    r[k] = v
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


def _open_row(source_key):
    import time
    return {
        "incident_id": f"inc_{source_key.replace(':', '_')}",
        "source_key": source_key,
        "status": "open",
        "detected_at": time.time() - 60,
        "resolution": None,
    }


class _SeedDB:
    def __init__(self, rows=None):
        self.incidents = _FakeIncidentColl(rows)


@pytest.mark.asyncio
async def test_resolve_recovered_tasks_resolves_alive_task():
    """An open supervised-task incident whose cron is alive again
    (re-supervised after a pod restart) must resolve."""
    db = _SeedDB([_open_row("supervised_task:alive_cron")])

    async def _sleeper():
        await asyncio.sleep(1000)  # cancelled at test end

    task = supervised_tasks.supervise(
        _sleeper(), name="alive_cron", db_getter=lambda: db,
        long_lived=True,
    )
    try:
        n = await supervised_tasks.resolve_recovered_tasks(lambda: db)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert n == 1
    row = db.incidents.rows[0]
    assert row["status"] == "resolved"
    assert "running again" in row["resolution"]


@pytest.mark.asyncio
async def test_resolve_recovered_tasks_leaves_dead_task_open():
    """An open supervised-task incident whose cron was never
    re-registered must stay OPEN for human eyes."""
    db = _SeedDB([_open_row("supervised_task:dead_cron")])

    n = await supervised_tasks.resolve_recovered_tasks(lambda: db)

    assert n == 0
    assert db.incidents.rows[0]["status"] == "open"


@pytest.mark.asyncio
async def test_resolve_recovered_tasks_ignores_other_guards():
    """The sweeper must not touch incidents it doesn't own —
    integration, LongCat and G19 rows stay open."""
    db = _SeedDB([
        _open_row("integration:stripe"),
        _open_row("longcat_stuck_degraded"),
        _open_row("process_recovery"),
    ])

    n = await supervised_tasks.resolve_recovered_tasks(lambda: db)

    assert n == 0
    assert all(r["status"] == "open" for r in db.incidents.rows)


@pytest.mark.asyncio
async def test_resolve_recovered_tasks_no_db_is_safe():
    """No db handle → 0 resolved, no crash (tick must never die)."""
    assert await supervised_tasks.resolve_recovered_tasks(lambda: None) == 0
    assert await supervised_tasks.resolve_recovered_tasks(None) == 0
