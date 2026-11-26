"""tests/test_p1g_loop_durable_queue.py — P1 durable job queue (P4).

Proves the P1g fix in services/loop_engine.py (ONLY file changed):

  * `LoopEngine.confirm()` / `start()` persist a durable job record
    (`loop_sessions.job`: status/worker_id/claimed_at/heartbeat_at)
    BEFORE the background task starts; a persist failure starts no
    task at all.
  * The owner refreshes `job.heartbeat_at` every JOB_HEARTBEAT_S and
    releases the record to its final status when the task ends.
  * `resume_stale()` rescues a job-orphaned session on the next tick
    (startup tick included) — mid-pipeline → PAUSED_FOR_USER for
    retry, mid-plan → FAILED — instead of waiting out STALE_AFTER_S
    (480 s), and now covers PLANNING at all (legacy path never did).
  * `lookup_or_rehydrate()` reconciles a provably-dead owner's
    mid-execution doc (CAS) and returns a resumable engine instead
    of None/404; a live owner's doc still refuses (no split-brain).

How it runs
-----------
The backend's full dependency tree is not installed in this sandbox,
so the REAL loop_engine module is loaded from this backend/ tree with
only two lightweight sys.modules stubs (loop_token_ledger, used by
start()'s plan scope; loop_safety, used for lock release / failure
recording) plus a fake in-memory Mongo. Tests are plain sync functions
driving coroutines with asyncio.run() — no pytest-asyncio needed.
Run from backend/:
    python -m pytest tests/test_p1g_loop_durable_queue.py -v

Deliberately NOT covered here (follow-up, not this lane):
  * True mid-phase resume (re-entering a phase where it stopped).
    Phases are not idempotent across restarts (see MAX_PHASE_RESTARTS),
    so an orphaned pipeline restarts from EXECUTE on retry — the
    established pause-response retry semantics. The durable record +
    reconciliation in this lane is the precondition for that work.
"""
from __future__ import annotations

import asyncio
import copy
import sys
import types
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# ─── Lightweight sys.modules stubs (import-time only) ────────────────
_RELEASES: list[dict] = []
_FAILURES: list[dict] = []


def _install_stubs():
    if "services.loop_token_ledger" not in sys.modules:
        m = types.ModuleType("services.loop_token_ledger")

        @asynccontextmanager
        async def loop_call_context(*_a, **_kw):
            yield None

        m.loop_call_context = loop_call_context
        sys.modules["services.loop_token_ledger"] = m
    if "services.loop_safety" not in sys.modules:
        m = types.ModuleType("services.loop_safety")

        async def release_loop_lock(db, project_id, user_id, loop_id):
            _RELEASES.append({"project_id": project_id,
                              "user_id": user_id, "loop_id": loop_id})

        async def record_loop_failure(db, proj_key, user_id, phase, reason):
            _FAILURES.append({"project_id": proj_key, "user_id": user_id,
                              "phase": phase, "reason": reason})

        async def acquire_loop_lock(*_a, **_kw):
            return True, None

        m.release_loop_lock = release_loop_lock
        m.record_loop_failure = record_loop_failure
        m.acquire_loop_lock = acquire_loop_lock
        sys.modules["services.loop_safety"] = m


_install_stubs()

from services import loop_engine as eng  # noqa: E402
from services.loop_engine import LoopEngine, LoopState  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_registry():
    eng.reset_registry()
    _RELEASES.clear()
    _FAILURES.clear()
    yield
    eng.reset_registry()
    _RELEASES.clear()
    _FAILURES.clear()


def run(coro):
    return asyncio.run(coro)


def _utc_now():
    return datetime.now(timezone.utc)


# ─── Fake Mongo ──────────────────────────────────────────────────────
_MISSING = object()


def _dget(row, dotted, default=None):
    cur = row
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _dset(row, dotted, value):
    parts = dotted.split(".")
    cur = row
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = copy.deepcopy(value)


def _matches(row, query):
    for key, val in query.items():
        if key == "$or":
            if not any(_matches(row, sub) for sub in val):
                return False
            continue
        actual = _dget(row, key, _MISSING)
        if isinstance(val, dict):
            if "$lt" in val:
                if actual is _MISSING or not (actual < val["$lt"]):
                    return False
            elif "$in" in val:
                if actual is _MISSING or actual not in val["$in"]:
                    return False
            elif "$exists" in val:
                if (actual is not _MISSING) != bool(val["$exists"]):
                    return False
            else:
                if actual != val:
                    return False
        else:
            if actual != val:
                return False
    return True


def _apply_update(row, update, *, is_insert=False):
    for key, val in (update.get("$set") or {}).items():
        _dset(row, key, val)
    if is_insert:
        for key, val in (update.get("$setOnInsert") or {}).items():
            _dset(row, key, val)
    for key, val in (update.get("$inc") or {}).items():
        _dset(row, key, _dget(row, key, 0) + val)
    for key, val in (update.get("$push") or {}).items():
        cur = _dget(row, key)
        if cur is None:
            cur = []
            _dset(row, key, cur)
        if isinstance(val, dict) and "$each" in val:
            cur.extend(copy.deepcopy(x) for x in val["$each"])
            sl = val.get("$slice")
            if isinstance(sl, int) and sl < 0 and len(cur) > -sl:
                del cur[:len(cur) + sl]
        else:
            cur.append(copy.deepcopy(val))


class _FakeResult:
    def __init__(self, modified_count=0, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _FakeColl:
    """Async Mongo collection fake: find_one / find / update_one /
    insert_one / find_one_and_update with $set (+dotted), $setOnInsert,
    $push, $inc, and filter ops $lt / $in / $exists / $or / dotted."""

    def __init__(self):
        self.rows: list[dict] = []
        self.update_calls: list[tuple] = []
        self.find_one_queries: list[dict] = []
        self.update_one_impl = None

    async def find_one(self, query, projection=None):
        self.find_one_queries.append(copy.deepcopy(query))
        for row in self.rows:
            if _matches(row, query):
                return copy.deepcopy(row)
        return None

    def find(self, query):
        matched = [copy.deepcopy(r) for r in self.rows
                   if _matches(r, query)]

        async def _gen():
            for row in matched:
                yield row

        return _gen()

    async def update_one(self, filt, update, upsert=False):
        self.update_calls.append((copy.deepcopy(filt),
                                  copy.deepcopy(update)))
        if self.update_one_impl is not None:
            return await self.update_one_impl(filt, update, upsert)
        for row in self.rows:
            if _matches(row, filt):
                _apply_update(row, update)
                return _FakeResult(modified_count=1)
        if upsert:
            doc = {}
            for key, val in filt.items():
                if not isinstance(val, dict) and not key.startswith("$"):
                    _dset(doc, key, val)
            _apply_update(doc, update, is_insert=True)
            self.rows.append(doc)
            return _FakeResult(modified_count=0, upserted_id="x")
        return _FakeResult(modified_count=0)

    async def find_one_and_update(self, filt, update, **_kw):
        for row in self.rows:
            if _matches(row, filt):
                _apply_update(row, update)
                return copy.deepcopy(row)
        return None

    async def insert_one(self, doc):
        self.rows.append(copy.deepcopy(doc))
        return SimpleNamespace(inserted_id="x")


class _FakeDB:
    """Attribute access returns a per-name fake collection."""

    def __init__(self):
        self._colls: dict[str, _FakeColl] = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        coll = self._colls.get(name)
        if coll is None:
            coll = _FakeColl()
            self._colls[name] = coll
        return coll

    def sessions(self):
        return self.loop_sessions

    def doc(self, loop_id):
        for row in self.loop_sessions.rows:
            if row.get("loop_id") == loop_id:
                return row
        return None


def _ctx(plan=True):
    return {
        "original_request": "do the thing",
        "plan": ({"title": "Plan", "files_to_change": ["a.py"],
                  "bullets": ["1. do"]} if plan else None),
        "files_changed": [],
        "errors_encountered": [],
        "self_heals_performed": [],
        "verification_results": {},
        "scan_results": {},
        "commit": None,
    }


def _session_doc(loop_id, state="executing", phase="execute", **extra):
    now = _utc_now()
    doc = {
        "loop_id": loop_id,
        "user_id": "u1",
        "project_id": "p1",
        "state": state,
        "phase": phase,
        "context": _ctx(),
        "updated_at": now,
        "created_at": now,
    }
    doc.update(extra)
    return doc


def _job(status="running", age_s=200, worker="dead-worker:9999"):
    now = _utc_now()
    return {
        "status": status,
        "worker_id": worker,
        "claimed_at": now - timedelta(seconds=age_s),
        "heartbeat_at": now - timedelta(seconds=age_s),
        "phase": "execute",
    }


# ─── 1. Pure orphan predicate ─────────────────────────────────────────
def test_job_is_orphaned_table():
    now = _utc_now()
    old = now - timedelta(seconds=eng.JOB_STALE_AFTER_S + 60)
    fresh = now - timedelta(seconds=5)
    very_old = now - timedelta(seconds=eng.STALE_AFTER_S + 60)

    # Stale heartbeat → orphaned.
    assert eng._job_is_orphaned(
        _session_doc("l", job=_job(age_s=200)), now) is True
    # Fresh heartbeat → live, never orphaned.
    assert eng._job_is_orphaned(
        _session_doc("l", job=_job(age_s=5)), now) is False
    # Missing heartbeat on a running job → orphaned (claim never
    # completed, so no owner can be heartbeating).
    job = _job(age_s=5)
    del job["heartbeat_at"]
    assert eng._job_is_orphaned(_session_doc("l", job=job), now) is True
    # Already-marked orphaned → still orphaned (idempotent).
    assert eng._job_is_orphaned(
        _session_doc("l", job=_job(status="orphaned")), now) is True
    # No job record: legacy bar — updated_at past STALE_AFTER_S.
    assert eng._job_is_orphaned(
        _session_doc("l", updated_at=very_old), now) is True
    assert eng._job_is_orphaned(
        _session_doc("l", updated_at=fresh), now) is False
    # No updated_at at all on a legacy doc → cannot prove death.
    doc = _session_doc("l")
    del doc["updated_at"]
    assert eng._job_is_orphaned(doc, now) is False
    # Terminal / paused / unknown states are never orphaned — a
    # FAILED loop must never be resurrected by the reconciler.
    for state in ("failed", "completed", "aborted", "expired",
                  "awaiting_confirmation", "paused_for_user",
                  "totally_made_up_state"):
        assert eng._job_is_orphaned(
            _session_doc("l", state=state, job=_job(age_s=200)),
            now) is False, state
    # Naive datetimes (what Motor actually returns) compare fine.
    doc = _session_doc("l", job=_job(age_s=200))
    doc["job"]["heartbeat_at"] = doc["job"]["heartbeat_at"].replace(
        tzinfo=None)
    assert eng._job_is_orphaned(doc, now) is True
    assert eng._job_is_orphaned("not-a-dict", now) is False
    assert eng._job_is_orphaned({}, now) is False


def test_job_final_status_table():
    assert eng._job_final_status(LoopState.COMPLETED) == "completed"
    assert eng._job_final_status(LoopState.FAILED) == "failed"
    assert eng._job_final_status(LoopState.ABORTED) == "aborted"
    assert eng._job_final_status(
        LoopState.AWAITING_CONFIRMATION) == "paused"
    assert eng._job_final_status(LoopState.PAUSED_FOR_USER) == "paused"
    assert eng._job_final_status(LoopState.EXECUTING) == "interrupted"


def test_doc_omits_job_when_unclaimed():
    engine = LoopEngine(db=None, loop_id="loop_x", user_id="u1",
                        project_id="p1", user_message="hi")
    assert "job" not in engine._doc()
    engine._job = {"status": "running", "worker_id": "w:1",
                   "claimed_at": _utc_now(), "heartbeat_at": _utc_now(),
                   "phase": "execute"}
    assert engine._doc()["job"]["status"] == "running"


# ─── 2. confirm(): claim before task, release on completion ──────────
def _engine_with_db(db, loop_id="loop_p1g_confirm"):
    engine = LoopEngine(db=db, loop_id=loop_id, user_id="u1",
                        project_id="p1", user_message="do the thing")
    engine.state = LoopState.AWAITING_CONFIRMATION
    engine.phase = "plan"
    engine.context["plan"] = {"title": "Plan",
                              "files_to_change": ["a.py"]}
    return engine


def test_confirm_claims_job_before_pipeline_starts():
    """The P1 core: the durable record exists BEFORE create_task,
    and the release lands the terminal job status afterwards."""
    db = _FakeDB()

    async def go():
        engine = _engine_with_db(db)

        async def _inner(self):
            self.state = LoopState.COMPLETED
            self.phase = "execute"
            await self._emit(LoopState.COMPLETED, "execute",
                             message="done")

        engine._run_pipeline_inner = _inner.__get__(engine, LoopEngine)
        await engine.confirm(True)
        assert engine._pipeline_task is not None
        await asyncio.wait_for(engine._pipeline_task, timeout=5)
        return engine

    engine = run(go())
    doc = db.doc("loop_p1g_confirm")
    assert doc is not None
    assert doc["state"] == "completed"
    assert doc["job"]["status"] == "completed"
    assert doc["job"]["worker_id"] == eng._WORKER_ID
    assert doc["job"]["phase"] == "pipeline"
    # Ordering: the claim persist (job running + still awaiting
    # confirmation) precedes every pipeline write.
    calls = db.loop_sessions.update_calls
    claim_idx = next(
        i for i, (_f, u) in enumerate(calls)
        if (u.get("$set") or {}).get("job", {}).get("status") == "running"
        and (u.get("$set") or {}).get("state") == "awaiting_confirmation")
    first_pipeline_idx = next(
        i for i, (_f, u) in enumerate(calls)
        if (u.get("$set") or {}).get("state") == "completed")
    assert claim_idx < first_pipeline_idx
    assert engine._job_heartbeat_task is None


def test_confirm_claim_failure_starts_no_pipeline():
    """Fail-closed: if the job record cannot be persisted, no
    pipeline task is created (no orphan task without a record)."""
    db = _FakeDB()

    async def _boom(_filt, _update, _upsert=False):
        raise RuntimeError("mongo down")

    db.loop_sessions.update_one_impl = _boom

    async def go():
        engine = _engine_with_db(db)
        with pytest.raises(RuntimeError, match="mongo down"):
            await engine.confirm(True)
        return engine

    engine = run(go())
    assert engine._pipeline_task is None
    assert engine._job_heartbeat_task is None
    assert engine._job == {}


def test_confirm_pipeline_cancel_releases_job_as_aborted():
    """Cancel mid-pipeline: the job record ends 'aborted', and the
    heartbeat task does not leak."""
    db = _FakeDB()
    started = asyncio.Event()

    async def go():
        engine = _engine_with_db(db)

        async def _inner(self):
            started.set()
            await asyncio.sleep(60)

        engine._run_pipeline_inner = _inner.__get__(engine, LoopEngine)
        await engine.confirm(True)
        task = engine._pipeline_task
        await asyncio.wait_for(started.wait(), timeout=5)
        await engine.cancel()
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.CancelledError:
                pass
        return engine

    engine = run(go())
    doc = db.doc("loop_p1g_confirm")
    assert doc["state"] == "aborted"
    assert doc["job"]["status"] == "aborted"
    assert engine._job_heartbeat_task is None


# ─── 3. Heartbeat ────────────────────────────────────────────────────
def test_heartbeat_tick_advances_lease_and_stops_when_lost():
    db = _FakeDB()
    old_hb = eng.JOB_HEARTBEAT_S
    eng.JOB_HEARTBEAT_S = 0.02
    try:
        async def go():
            engine = _engine_with_db(db)
            await engine._claim_job("pipeline")
            first = engine._job["heartbeat_at"]
            await asyncio.sleep(0.08)
            # A few ticks must have refreshed the lease in Mongo.
            doc = db.doc("loop_p1g_confirm")
            assert doc["job"]["heartbeat_at"] > first
            # Ownership lost (job no longer running) → tick False.
            doc["job"]["status"] = "orphaned"
            assert await engine._job_heartbeat_tick() is False
            await engine._release_job()
            return engine

        engine = run(go())
        assert engine._job_heartbeat_task is None
        assert db.doc("loop_p1g_confirm")["job"]["status"] == "paused"
    finally:
        eng.JOB_HEARTBEAT_S = old_hb


def test_heartbeat_tick_db_error_keeps_ticking():
    """A transient DB blip must not kill the pipeline (and must not
    orphan a live worker — the 90 s lease tolerates missed beats)."""
    db = _FakeDB()

    async def go():
        engine = _engine_with_db(db)
        await engine._claim_job("pipeline")
        assert await engine._job_heartbeat_tick() is True

        async def _boom(*_a, **_kw):
            raise RuntimeError("mongo blip")

        db.loop_sessions.update_one = _boom
        assert await engine._job_heartbeat_tick() is True
        await engine._release_job()
        return engine

    run(go())


# ─── 4. start(): claim before first emit, release as paused ──────────
def test_start_claims_job_before_first_emit():
    db = _FakeDB()

    async def go():
        engine = LoopEngine(db=db, loop_id="loop_p1g_start", user_id="u1",
                            project_id="p1", user_message="do the thing")

        async def _fake_plan(self):
            self.context["plan"] = {"title": "Plan",
                                    "files_to_change": ["a.py"]}
            self.state = LoopState.AWAITING_CONFIRMATION
            self.phase = "plan"
            await _persist_session_helper(self)

        async def _persist_session_helper(self):
            from services.loop_engine_helpers import _persist_session
            await _persist_session(self.db, self._doc())

        engine._do_plan = _fake_plan.__get__(engine, LoopEngine)
        events = [ev async for ev in engine.start()]
        return engine, events

    engine, events = run(go())
    assert engine.state == LoopState.AWAITING_CONFIRMATION
    assert events, "plan events must still be yielded"
    doc = db.doc("loop_p1g_start")
    assert doc is not None, "session doc must exist after start()"
    assert doc["job"]["status"] == "paused"
    assert doc["job"]["worker_id"] == eng._WORKER_ID
    # The FIRST persist already carried the running job record.
    first = db.loop_sessions.update_calls[0][1].get("$set") or {}
    assert first.get("job", {}).get("status") == "running"
    assert first.get("state") == "planning"
    assert engine._job_heartbeat_task is None


def test_start_crash_releases_job_and_leaks_no_heartbeat():
    """A plan crash still ends the lease (failed) and never leaves
    a heartbeat task refreshing a dead loop's lease."""
    db = _FakeDB()

    async def go():
        engine = LoopEngine(db=db, loop_id="loop_p1g_start_crash",
                            user_id="u1", project_id="p1",
                            user_message="do the thing")

        async def _boom_plan(self):
            raise RuntimeError("plan LLM exploded")

        engine._do_plan = _boom_plan.__get__(engine, LoopEngine)
        events = [ev async for ev in engine.start()]
        return engine, events

    engine, events = run(go())
    assert engine.state == LoopState.FAILED
    assert engine._job_heartbeat_task is None
    doc = db.doc("loop_p1g_start_crash")
    assert doc["state"] == "failed"
    assert doc["job"]["status"] == "failed"


# ─── 5. Worker restart: lookup + sweeper ─────────────────────────────
def _seed_orphaned_pipeline(db, loop_id="loop_p1g_orphan"):
    db.loop_sessions.rows.append(_session_doc(
        loop_id, state="executing", phase="execute",
        job=_job(age_s=200),
        updated_at=_utc_now() - timedelta(seconds=200)))
    return loop_id


def test_restart_orphaned_pipeline_lookup_returns_resumable_engine():
    """THE restart proof: a pipeline whose worker died (frozen
    heartbeat) is visible afterwards as a resumable paused engine —
    never a 404 with no record."""
    db = _FakeDB()
    loop_id = _seed_orphaned_pipeline(db)

    async def go():
        # Pre-condition: the doc exists and says EXECUTING — the
        # exact state the old code refused with a 404.
        before = await eng.load_session(db, loop_id)
        assert before["state"] == "executing"
        assert before["job"]["status"] == "running"
        result = await eng.lookup_or_rehydrate(db, loop_id)
        assert result is not None, "orphaned loop must not 404"
        assert result.loop_id == loop_id
        assert result.state == LoopState.PAUSED_FOR_USER
        assert result.phase == "execute"
        assert result.context["plan"]["title"] == "Plan"
        assert eng.lookup(loop_id) is result
        after = await eng.load_session(db, loop_id)
        assert after["state"] == "paused_for_user"
        assert after["resume_reason"] == "worker_restart_mid_loop"
        assert after["last_event"]["data"]["rescued"] is True
        assert after["last_event"]["requires_user_action"] is True
        assert after["job"]["status"] == "orphaned"
        return result

    run(go())


def test_restart_live_owner_still_refuses():
    """Split-brain guard intact: a FRESH heartbeat means the loop
    is alive on another worker — lookup still refuses (404)."""
    db = _FakeDB()
    db.loop_sessions.rows.append(_session_doc(
        "loop_live", state="executing", phase="execute",
        job=_job(age_s=5), updated_at=_utc_now()))
    assert run(eng.lookup_or_rehydrate(db, "loop_live")) is None
    assert run(eng.resume_stale(db)) == 0
    assert db.doc("loop_live")["state"] == "executing"


def test_restart_legacy_doc_without_job_rescued():
    """Pre-upgrade docs (no job record) keep the old rescue bar:
    stale updated_at → paused on lookup; fresh → still refused."""
    db = _FakeDB()
    db.loop_sessions.rows.append(_session_doc(
        "loop_legacy", state="verifying", phase="verify",
        updated_at=_utc_now() - timedelta(seconds=600)))
    db.loop_sessions.rows.append(_session_doc(
        "loop_legacy_fresh", state="executing", phase="execute",
        updated_at=_utc_now()))

    async def go():
        result = await eng.lookup_or_rehydrate(db, "loop_legacy")
        assert result is not None
        assert result.state == LoopState.PAUSED_FOR_USER
        assert db.doc("loop_legacy")["resume_reason"] == (
            "worker_restart_mid_loop")
        assert await eng.lookup_or_rehydrate(db, "loop_legacy_fresh") is None

    run(go())


def test_resume_stale_fast_path_rescues_all_three_kinds():
    """One sweeper tick (the same call main.py's housekeeping makes
    on its first startup tick) rescues: a job-orphaned pipeline, a
    job-orphaned plan, and a legacy stale doc — and leaves live and
    terminal docs alone."""
    db = _FakeDB()
    now = _utc_now()
    db.loop_sessions.rows.append(_session_doc(
        "loop_fast", state="executing", phase="execute",
        job=_job(age_s=200), updated_at=now - timedelta(seconds=200)))
    db.loop_sessions.rows.append(_session_doc(
        "loop_plan", state="planning", phase="plan",
        job=_job(age_s=200), updated_at=now - timedelta(seconds=200)))
    db.loop_sessions.rows.append(_session_doc(
        "loop_legacy", state="scanning", phase="scan",
        updated_at=now - timedelta(seconds=600)))
    db.loop_sessions.rows.append(_session_doc(
        "loop_live", state="executing", phase="execute",
        job=_job(age_s=5), updated_at=now))
    db.loop_sessions.rows.append(_session_doc(
        "loop_term", state="failed", phase="execute",
        job=_job(age_s=200), updated_at=now - timedelta(seconds=200)))

    rescued = run(eng.resume_stale(db))
    assert rescued == 3, f"expected 3 rescues, got {rescued}"

    fast = db.doc("loop_fast")
    assert fast["state"] == "paused_for_user"
    assert fast["resume_reason"] == "worker_restart_mid_loop"
    assert fast["last_event"]["data"]["rescued"] is True
    assert fast["job"]["status"] == "orphaned"

    plan = db.doc("loop_plan")
    assert plan["state"] == "failed"
    assert plan["resume_reason"] == "worker_restart_mid_plan"
    assert plan["job"]["status"] == "orphaned"
    # Mid-plan death releases the project lock (fresh start works).
    assert any(r.get("loop_id") == "loop_plan" for r in _RELEASES)

    legacy = db.doc("loop_legacy")
    assert legacy["state"] == "paused_for_user"
    assert legacy["resume_reason"] == "server_restart_mid_loop"

    assert db.doc("loop_live")["state"] == "executing"
    assert db.doc("loop_term")["state"] == "failed"

    # Second tick: nothing left to rescue (no double-count).
    assert run(eng.resume_stale(db)) == 0


def test_reconcile_cas_loses_when_owner_heartbeats_late():
    """A stale snapshot in hand but a FRESH row in Mongo (the owner
    heartbeated after our read) must lose the CAS — no clobber."""
    db = _FakeDB()
    db.loop_sessions.rows.append(_session_doc(
        "loop_race", state="executing", phase="execute",
        job=_job(age_s=5), updated_at=_utc_now()))
    stale_copy = copy.deepcopy(db.doc("loop_race"))
    stale_copy["job"]["heartbeat_at"] = (
        _utc_now() - timedelta(seconds=300))
    assert run(eng._reconcile_orphaned_job(db, stale_copy)) is None
    assert db.doc("loop_race")["state"] == "executing"
    assert db.doc("loop_race")["job"]["status"] == "running"


def test_reconcile_twice_second_returns_none():
    """Re-reconciling an already-reconciled doc is a no-op."""
    db = _FakeDB()
    loop_id = _seed_orphaned_pipeline(db)
    first = run(eng._reconcile_orphaned_job(db, db.doc(loop_id)))
    assert first is not None
    assert first["state"] == "paused_for_user"
    second = run(eng._reconcile_orphaned_job(db, db.doc(loop_id)))
    assert second is None


def test_reconcile_mid_plan_death_fails_not_pauses():
    """A plan-phase orphan has no plan to resume: FAILED (visible,
    lock released), never PAUSED — retrying it would run EXECUTE
    with plan=None."""
    db = _FakeDB()
    db.loop_sessions.rows.append(_session_doc(
        "loop_plan2", state="planning", phase="plan",
        job=_job(age_s=200), updated_at=_utc_now() - timedelta(seconds=200)))
    # Lookup must NOT hand back a rehydrated engine for a FAILED
    # reconcile (the doc itself is the answer; the router's
    # terminal guard turns a retry into 409, not 404).
    assert run(eng.lookup_or_rehydrate(db, "loop_plan2")) is None
    doc = db.doc("loop_plan2")
    assert doc["state"] == "failed"
    assert doc["resume_reason"] == "worker_restart_mid_plan"
    assert any(r.get("loop_id") == "loop_plan2" for r in _RELEASES)


# ─── 6. Unchanged behaviour (negative controls) ──────────────────────
def test_paused_rehydration_unchanged():
    """The existing paused-rehydration path is untouched."""
    db = _FakeDB()
    db.loop_sessions.rows.append(_session_doc(
        "loop_paused", state="awaiting_confirmation", phase="plan"))
    result = run(eng.lookup_or_rehydrate(db, "loop_paused"))
    assert result is not None
    assert result.state == LoopState.AWAITING_CONFIRMATION
    assert result.context["plan"]["title"] == "Plan"
    assert eng.lookup("loop_paused") is result


def test_terminal_missing_unknown_still_none():
    """Nonexistent / terminal / unknown-state docs still refuse —
    the reconciler never resurrects them (mirrors iter 212m-144)."""
    db = _FakeDB()
    assert run(eng.lookup_or_rehydrate(db, "loop_missing")) is None
    db.loop_sessions.rows.append(_session_doc("loop_term2",
                                              state="failed"))
    assert run(eng.lookup_or_rehydrate(db, "loop_term2")) is None
    doc = _session_doc("loop_unk", state="awaiting_confirmation")
    doc["state"] = "totally_made_up_state"
    db.loop_sessions.rows.append(doc)
    assert run(eng.lookup_or_rehydrate(db, "loop_unk")) is None
    assert run(eng.lookup_or_rehydrate(None, "anything")) is None

def test_live_pipeline_restart_end_to_end():
    """Task-spec scenario, end to end: start a loop (confirm a
    pipeline), simulate a worker restart by dropping the in-memory
    task + registry WITHOUT any cleanup, then prove the job is
    visible afterwards as resumable — never a 404 with no record.

    Time travel is explicit: after "killing" the worker we backdate
    the stored heartbeat 200 s (what a restart 200 s later sees).
    The abandoned pipeline task is cancelled only at the very end;
    its finally block then persists from the dead engine — a
    test-only artefact (a real dead worker has no finally), so all
    assertions run before cleanup.
    """
    db = _FakeDB()
    blocked = asyncio.Event()

    async def go():
        engine = _engine_with_db(db)

        async def _inner(self):
            # Mirror the real _do_execute entry: state flips to
            # EXECUTING on the first emit, before any long work.
            self.state = LoopState.EXECUTING
            self.phase = "execute"
            await self._emit(LoopState.EXECUTING, "execute",
                             message="Executing…")
            await blocked.wait()

        engine._run_pipeline_inner = _inner.__get__(engine, LoopEngine)
        await engine.confirm(True)
        assert engine._pipeline_task is not None
        await asyncio.sleep(0.05)
        # The job record exists while the pipeline runs.
        doc = db.doc("loop_p1g_confirm")
        assert doc["job"]["status"] == "running"

        # ── Simulate the worker dying: no cancel, no release, no
        #    graceful shutdown. Kill only the lease ticker (a dead
        #    process heartbeats nothing) and drop the registry.
        hb = engine._job_heartbeat_task
        if hb is not None:
            hb.cancel()
            try:
                await hb
            except asyncio.CancelledError:
                pass
        engine._job_heartbeat_task = None
        eng.reset_registry()
        pipeline_task = engine._pipeline_task
        # 200 s pass; a new worker boots and looks the loop up.
        doc["job"]["heartbeat_at"] = (
            _utc_now() - timedelta(seconds=200))
        doc["updated_at"] = _utc_now() - timedelta(seconds=200)

        result = await eng.lookup_or_rehydrate(db, "loop_p1g_confirm")
        assert result is not None, "restart must not 404 the loop"
        assert result.state == LoopState.PAUSED_FOR_USER
        assert result.phase == "execute"
        after = await eng.load_session(db, "loop_p1g_confirm")
        assert after["state"] == "paused_for_user"
        assert after["resume_reason"] == "worker_restart_mid_loop"
        assert after["last_event"]["data"]["rescued"] is True

        # Cleanup only: cancel the abandoned task (its finally
        # persists from the dead engine — ignored, assertions done).
        blocked.set()
        pipeline_task.cancel()
        try:
            await pipeline_task
        except asyncio.CancelledError:
            pass

    run(go())
