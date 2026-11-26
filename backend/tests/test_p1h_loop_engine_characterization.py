"""P1H — characterization tests for services/loop_engine.py.

Phase 1 of the loop_engine decomposition (ORA audit, lane p1h).

Purpose: pin the CURRENT behaviour of the 4,978-line loop_engine
module at its public seams so any later split can be verified as
behaviour-identical. These tests:

  * run against the PRISTINE tree (baseline) and must keep passing
    after the loop_engine_plan extraction and after every later split;
  * use a fake in-memory Mongo (FakeDB) and stub out every lazily
    imported service (LLM, parliament, loop_safety, ...) in
    sys.modules, so they run with stdlib only — no app bootstrap,
    no network, no real Mongo;
  * NEVER touch the `_parl.heal` call site (findings lane), the mode
    threading, or the snapshot/push paths (part13 lane).

Run:  python3 -m unittest discover -s backend/tests -p "test_p1h_*.py" -t backend
  (or pytest, which also collects unittest.TestCase classes).

Seams pinned here:
  1. module constants / invariants (budgets, caps, timeouts)
  2. LoopState values + event factory shape + leak-token stripping
  3. _LIVE registry + lookup_or_rehydrate (incl. split-brain guard)
  4. housekeeping sweepers resume_stale / sweep_expired_* (Mongo contracts)
  5. LoopEngine.__init__ defaults + _doc()
  6. _emit / _narrate / _fail (SSE + Mongo contracts, stall detector)
  7. _should_stop matrix + _with_budget restart semantics
  8. _generate_plan (plan-LLM adapter) incl. timeout + cost-cap
  9. _do_verify global self-heal cap (healer itself NOT invoked)
  10. start() plan-phase happy path
"""
import asyncio
import contextlib
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.loop_engine as le  # noqa: E402
from services.loop_engine import LoopEngine, LoopState  # noqa: E402


# ─── Fake Mongo ──────────────────────────────────────────────────────

def _matches(doc, filt):
    for key, val in filt.items():
        cur = doc.get(key)
        if isinstance(val, dict):
            if "$in" in val:
                if cur not in val["$in"]:
                    return False
            elif "$lt" in val:
                if cur is None or not cur < val["$lt"]:
                    return False
            elif "$exists" in val:
                if (key in doc) != bool(val["$exists"]):
                    return False
            else:  # unknown operator -> no match (fail loudly in tests)
                return False
        else:
            if cur != val:
                return False
    return True


def _apply_update(doc, update):
    for op, changes in update.items():
        if op == "$set":
            doc.update(changes)
        elif op == "$setOnInsert":
            for key, val in changes.items():
                doc.setdefault(key, val)
        elif op == "$push":
            for key, spec in changes.items():
                each = spec.get("$each", [])
                sl = spec.get("$slice")
                cur = doc.setdefault(key, [])
                cur.extend(each)
                if sl is not None and sl < 0:
                    doc[key] = cur[sl:]
        else:  # pragma: no cover - unknown operator in a test
            raise AssertionError(f"FakeCollection: unsupported update op {op!r}")


class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def _gen():
            for doc in self._docs:
                yield doc
        return _gen()


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.docs = []
        self.calls = []
        self.raise_on_find = None

    async def insert_one(self, doc):
        self.calls.append(("insert_one", dict(doc)))
        self.docs.append(dict(doc))
        return {"inserted_id": len(self.docs)}

    async def find_one(self, filt, projection=None):
        self.calls.append(("find_one", dict(filt)))
        for doc in self.docs:
            if _matches(doc, filt):
                return dict(doc)
        return None

    def find(self, filt):
        if self.raise_on_find is not None:
            raise self.raise_on_find
        return FakeCursor([d for d in self.docs if _matches(d, filt)])

    async def update_one(self, filt, update, upsert=False):
        self.calls.append(("update_one", dict(filt), update, upsert))
        for doc in self.docs:
            if _matches(doc, filt):
                _apply_update(doc, update)
                return {"matched_count": 1}
        if upsert:
            new_doc = {k: v for k, v in filt.items()
                       if not isinstance(v, dict)}
            _apply_update(new_doc, update)
            self.docs.append(new_doc)
            return {"upserted_count": 1}
        return {"matched_count": 0}

    async def find_one_and_update(self, filt, update, projection=None):
        self.calls.append(("find_one_and_update", dict(filt)))
        for doc in self.docs:
            if _matches(doc, filt):
                _apply_update(doc, update)
                return dict(doc)
        return None


class FakeDB:
    """Attribute access creates collections on demand (like Motor)."""

    def __init__(self):
        self._cols = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        col = self._cols.get(name)
        if col is None:
            col = FakeCollection(name)
            self._cols[name] = col
        return col

    def col(self, name):
        return self.__getattr__(name)


def _utcnow():
    return datetime.now(timezone.utc)


# ─── Stub modules (lazily imported by loop_engine) ───────────────────

def _make_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, val in attrs.items():
        setattr(mod, key, val)
    sys.modules[name] = mod
    return mod


class StubWorld(unittest.IsolatedAsyncioTestCase):
    """Base: installs stub services in sys.modules; restores on teardown."""

    def setUp(self):
        super().setUp()
        self._saved_modules = {}
        self.db = FakeDB()
        self.calls = {"loop_beta": [], "funnel": [], "sse": [],
                      "release_lock": [], "record_failure": [],
                      "intent_stat": [], "audit": [], "freeze": [],
                      "build_graph": [], "llm": [], "wallet": []}
        calls = self.calls

        @contextlib.asynccontextmanager
        async def _loop_call_context(*args, **kwargs):
            yield

        async def _log_execution(db, **kw):
            calls["loop_beta"].append(kw)
            return None

        async def _emit_funnel_event(db, **kw):
            calls["funnel"].append(kw)
            return None

        def _sse_record(loop_id, ev):
            calls["sse"].append((loop_id, ev))

        async def _release_loop_lock(db, project_id, user_id, loop_id):
            calls["release_lock"].append((project_id, user_id, loop_id))

        async def _record_loop_failure(db, proj_key, user_id, phase, reason):
            calls["record_failure"].append((proj_key, user_id, phase, reason))

        async def _record_intent_stat(db, kind):
            calls["intent_stat"].append(kind)

        async def _audit_log(db, **kw):
            calls["audit"].append(kw)

        async def _freeze(db, **kw):
            calls["freeze"].append(kw)

        async def _finalize_hold(db, **kw):
            calls["wallet"].append(kw)
            return None

        async def _verify_files(file_objs):
            return {"ok": True, "results": [], "errors": []}

        async def _build_repo_map(db, project_id, user_id):
            return {"has_map": False}

        async def _call_llm_with_meta(sys_msg, user_message, **kw):
            calls["llm"].append({"sys_msg": sys_msg,
                                 "user_message": user_message, **kw})
            return {"content": json.dumps({
                "title": "Test plan",
                "files_to_change": ["a.py"],
                "bullets": ["Step one"],
                "estimated_time": "~1 minute"})}

        def _get_db():
            raise RuntimeError("no db in test")

        async def _build_graph(**kw):
            calls["build_graph"].append(kw)

        async def _get_repo_token_or_error(proj):
            return (None, "no_token", None)

        async def _build_ora_context(**kw):
            raise RuntimeError("no PAT in test")

        def _check_file_integrity(**kw):
            return None

        def _build_evidence_message(**kw):
            return {"text": "evidence", "evidence_ref": {"kind": "loop_run"}}

        self._install("services.loop_token_ledger",
                      loop_call_context=_loop_call_context)
        self._install("services.loop_beta", log_execution=_log_execution)
        self._install("services.signup_guards",
                      emit_funnel_event=_emit_funnel_event)
        self._install("services.sse_replay_buffer", record=_sse_record)
        self._install("services.loop_safety",
                      release_loop_lock=_release_loop_lock,
                      record_loop_failure=_record_loop_failure)
        self._install("services.loop_intent_stats",
                      record_intent_stat=_record_intent_stat)
        self._install("services.loop_audit_log", log=_audit_log,
                      KIND_SILENT_CATCH="silent_catch",
                      KIND_VANGUARD="vanguard",
                      VERDICT_FAIL="fail", VERDICT_PASS="pass",
                      VERDICT_WARN="warn")
        self._install("services.loop_task_specs", freeze=_freeze)
        self._install("services.wallet", finalize_hold=_finalize_hold)
        self._install("services.loop_verify", verify_files=_verify_files)
        self._install("services.repo_map", build_repo_map=_build_repo_map)
        self._install("services.identity",
                      OR_IDENTITY="You are ORA, an AI CTO. ")
        self._install("services.llm",
                      call_llm_with_meta=_call_llm_with_meta)
        self._install("cto_services")
        self._install("cto_services.db", get_db=_get_db)
        self._install("services.graph_builder", build_graph=_build_graph)
        self._install("services.pat_vault",
                      get_repo_token_or_error=_get_repo_token_or_error)
        self._install("services.ora_context",
                      build_ora_context=_build_ora_context)
        self._install("services.loop_integrity_guard",
                      check_file_integrity=_check_file_integrity)
        self._install("services.evidence_pointer",
                      build_evidence_message=_build_evidence_message)
        le.reset_registry()

    def _install(self, name, **attrs):
        if name not in self._saved_modules:
            self._saved_modules[name] = sys.modules.get(name)
        _make_module(name, **attrs)

    def tearDown(self):
        for name, prev in self._saved_modules.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
        le.reset_registry()
        super().tearDown()

    def engine(self, loop_id="loop_test1", user_id="u1",
               project_id=None, user_message="do the thing",
               session_id=None, **kw):
        return LoopEngine(self.db, loop_id, user_id, project_id,
                          user_message, session_id=session_id, **kw)


# ─── 1. Module constants / invariants ────────────────────────────────

class TestConstants(StubWorld):
    def test_phase_timeouts_exact(self):
        self.assertEqual(le.PHASE_TIMEOUTS_S, {
            "plan": 120, "execute": 420, "verify": 360,
            "scan": 180, "ship": 120, "self_heal": 180})

    def test_stale_after_exceeds_largest_budget(self):
        self.assertEqual(le.STALE_AFTER_S,
                         max(le.PHASE_TIMEOUTS_S.values()) + 60)
        self.assertEqual(le.STALE_AFTER_S, 480)

    def test_caps_and_timeouts(self):
        self.assertEqual(le.MAX_SELF_HEALS, 2)
        self.assertEqual(le.SELF_HEAL_LLM_TIMEOUT_S, 60)
        self.assertEqual(le.LOOP_TOTAL_BUDGET_S, 1800)
        self.assertEqual(le.PLAN_LLM_TIMEOUT_S, 30)
        self.assertEqual(le.AWAITING_CONFIRM_MAX_S, 600)
        self.assertEqual(le.HEARTBEAT_INTERVAL_S, 6.0)
        self.assertEqual(le.MAX_PHASE_RESTARTS, 1)

    def test_plan_ttl_reexport(self):
        self.assertEqual(le.PLAN_TTL_S, 24 * 60 * 60)

    def test_loop_state_values(self):
        self.assertEqual({m.value for m in LoopState}, {
            "idle", "planning", "awaiting_confirmation", "executing",
            "verifying", "scanning", "shipping", "self_healing",
            "paused_for_user", "completed", "failed", "aborted",
            "expired"})
        self.assertEqual(len(LoopState), 13)

    def test_new_loop_id_shape(self):
        lid = le.new_loop_id()
        self.assertRegex(lid, r"^loop_[0-9a-f]{14}$")

    def test_terminal_set(self):
        self.assertEqual(le._TERMINAL, {
            LoopState.COMPLETED, LoopState.FAILED, LoopState.ABORTED})


# ─── 2. Event factory + leak-token stripping ─────────────────────────

class TestEvents(StubWorld):
    def test_new_event_shape(self):
        ev = le.new_event("loop_1", LoopState.EXECUTING, "execute",
                          step=2, total_steps=5, message="Working",
                          data={"k": "v"}, requires_user_action=False)
        self.assertEqual(ev["loop_id"], "loop_1")
        self.assertEqual(ev["state"], "executing")
        self.assertEqual(ev["phase"], "execute")
        self.assertEqual(ev["step"], 2)
        self.assertEqual(ev["total_steps"], 5)
        self.assertEqual(ev["message"], "Working")
        self.assertEqual(ev["data"], {"k": "v"})
        self.assertFalse(ev["requires_user_action"])
        self.assertIn("timestamp", ev)

    def test_new_event_defaults(self):
        ev = le.new_event("loop_1", LoopState.IDLE, "idle")
        self.assertEqual(ev["step"], 0)
        self.assertEqual(ev["data"], {})
        self.assertFalse(ev["requires_user_action"])

    def test_strip_engine_leak_tokens(self):
        self.assertEqual(
            le._strip_engine_leak_tokens("Fix the e2b sandbox"),
            "Fix the sandbox sandbox")
        self.assertEqual(
            le._strip_engine_leak_tokens("E2B is Disabled By Admin"),
            "sandbox is unavailable right now")
        # Vanguard is a public product name, NOT a leak.
        self.assertEqual(
            le._strip_engine_leak_tokens("Vanguard scan passed"),
            "Vanguard scan passed")
        # User's own repo path is untouched.
        self.assertEqual(
            le._strip_engine_leak_tokens("Committing 3 file(s) to a/b"),
            "Committing 3 file(s) to a/b")

    async def test_record_backup_and_rollback_roundtrip(self):
        await le.record_backup(self.db, "loop_1", "a.py", "old")
        await le.record_backup(self.db, "loop_1", "b.py", "older")
        out = await le.rollback(self.db, "loop_1")
        self.assertEqual(out, [{"path": "a.py", "content": "old"},
                               {"path": "b.py", "content": "older"}])
        # A different loop_id sees nothing (Mongo contract: loop-scoped).
        self.assertEqual(await le.rollback(self.db, "loop_2"), [])


# ─── 3. Registry + rehydration ───────────────────────────────────────

class TestRegistry(StubWorld):
    def test_register_lookup_deregister(self):
        eng = self.engine()
        self.assertIsNone(le.lookup("loop_test1"))
        le.register(eng)
        self.assertIs(le.lookup("loop_test1"), eng)
        le.deregister("loop_test1")
        self.assertIsNone(le.lookup("loop_test1"))

    def test_reset_registry(self):
        le.register(self.engine())
        le.reset_registry()
        self.assertIsNone(le.lookup("loop_test1"))

    async def test_lookup_or_rehydrate_returns_live_engine(self):
        eng = self.engine()
        le.register(eng)
        got = await le.lookup_or_rehydrate(self.db, "loop_test1")
        self.assertIs(got, eng)

    async def test_lookup_or_rehydrate_returns_none_without_doc(self):
        self.assertIsNone(
            await le.lookup_or_rehydrate(self.db, "loop_missing"))

    async def test_lookup_or_rehydrate_refuses_mid_execution(self):
        self.db.loop_sessions.docs.append({
            "loop_id": "loop_mid", "state": "executing",
            "phase": "execute", "user_id": "u1",
            "project_id": "p1", "context": {}})
        got = await le.lookup_or_rehydrate(self.db, "loop_mid")
        self.assertIsNone(got)
        self.assertIsNone(le.lookup("loop_mid"))

    async def test_lookup_or_rehydrate_rehydrates_paused_loop(self):
        self.db.loop_sessions.docs.append({
            "loop_id": "loop_paused", "state": "paused_for_user",
            "phase": "execute", "user_id": "u1",
            "project_id": "p1",
            "context": {"original_request": "do the thing",
                        "plan": {"title": "T"}}})
        eng = await le.lookup_or_rehydrate(self.db, "loop_paused")
        self.assertIsNotNone(eng)
        self.assertEqual(eng.state, LoopState.PAUSED_FOR_USER)
        self.assertEqual(eng.phase, "execute")
        self.assertEqual(eng.context["plan"], {"title": "T"})
        # bin_ctx rebuild failed (stub raises) -> None, not a crash.
        self.assertIsNone(eng.bin_ctx)
        self.assertIs(le.lookup("loop_paused"), eng)

    async def test_lookup_or_rehydrate_stale_local_engine_evicted(self):
        eng = self.engine(loop_id="loop_stale")
        eng.state = LoopState.AWAITING_CONFIRMATION
        eng.phase = "plan"
        le.register(eng)
        # Mongo disagrees: the loop advanced elsewhere.
        self.db.loop_sessions.docs.append({
            "loop_id": "loop_stale", "state": "executing",
            "phase": "execute", "user_id": "u1",
            "project_id": "p1", "context": {}})
        got = await le.lookup_or_rehydrate(self.db, "loop_stale")
        self.assertIsNone(got)  # mid-execution -> refuse, no stale reuse
        self.assertIsNone(le.lookup("loop_stale"))


# ─── 4. Housekeeping sweepers ────────────────────────────────────────

class TestHousekeeping(StubWorld):
    def _stale_doc(self, loop_id, state, age_s=1000, **extra):
        doc = {"loop_id": loop_id, "state": state,
               "phase": "execute", "user_id": "u1",
               "project_id": "p1",
               "updated_at": _utcnow() - timedelta(seconds=age_s)}
        doc.update(extra)
        self.db.loop_sessions.docs.append(doc)
        return doc

    async def test_resume_stale_rescues_stuck_execute(self):
        self._stale_doc("loop_old", "executing")
        n = await le.resume_stale(self.db)
        self.assertEqual(n, 1)
        doc = self.db.loop_sessions.docs[0]
        self.assertEqual(doc["state"], "paused_for_user")
        self.assertEqual(doc["resume_reason"], "server_restart_mid_loop")
        ev = doc["last_event"]
        self.assertEqual(ev["state"], "paused_for_user")
        self.assertTrue(ev["requires_user_action"])
        self.assertTrue(ev["data"]["rescued"])
        self.assertEqual(ev["data"]["resume_reason"],
                         "server_restart_mid_loop")
        # G1: the rescue is logged to loop_errors.
        self.assertEqual(len(self.db.loop_errors.docs), 1)
        self.assertEqual(self.db.loop_errors.docs[0]["loop_id"], "loop_old")

    async def test_resume_stale_ignores_fresh_and_terminal(self):
        self._stale_doc("loop_old", "executing")
        self._stale_doc("loop_fresh", "executing", age_s=10)
        self._stale_doc("loop_done", "completed")
        n = await le.resume_stale(self.db)
        self.assertEqual(n, 1)
        states = {d["loop_id"]: d["state"]
                  for d in self.db.loop_sessions.docs}
        self.assertEqual(states["loop_fresh"], "executing")
        self.assertEqual(states["loop_done"], "completed")

    async def test_resume_stale_covers_all_running_states(self):
        for i, state in enumerate(
                ["executing", "verifying", "scanning", "shipping",
                 "self_healing"]):
            self._stale_doc(f"loop_{i}", state)
        self.assertEqual(await le.resume_stale(self.db), 5)

    async def test_sweep_expires_stale_awaiting_confirmation(self):
        eng = self.engine(loop_id="loop_exp")
        le.register(eng)
        self._stale_doc("loop_exp", "awaiting_confirmation")
        n = await le.sweep_expired_awaiting_confirmations(self.db)
        self.assertEqual(n, 1)
        doc = self.db.loop_sessions.docs[0]
        self.assertEqual(doc["state"], "expired")
        self.assertEqual(doc["resume_reason"],
                         "awaiting_confirmation_timeout")
        # Lock released with the full 4-tuple (I1 fix).
        self.assertEqual(self.calls["release_lock"],
                         [("p1", "u1", "loop_exp")])
        # Dropped from the in-process registry.
        self.assertIsNone(le.lookup("loop_exp"))
        self.assertEqual(len(self.db.loop_errors.docs), 1)

    async def test_sweep_ignores_fresh_paused(self):
        self._stale_doc("loop_old", "paused_for_user")
        self._stale_doc("loop_fresh", "paused_for_user", age_s=10)
        self.assertEqual(
            await le.sweep_expired_awaiting_confirmations(self.db), 1)

    async def test_sweep_cursor_failure_returns_zero(self):
        self.db.loop_sessions.raise_on_find = RuntimeError("mongo down")
        self.assertEqual(
            await le.sweep_expired_awaiting_confirmations(self.db), 0)

    async def test_sweep_lock_release_failure_is_best_effort(self):
        async def _boom(*args, **kwargs):
            raise RuntimeError("lock service down")
        sys.modules["services.loop_safety"].release_loop_lock = _boom
        self._stale_doc("loop_exp2", "awaiting_confirmation")
        # Still expires the loop; the lock TTL clears it later.
        self.assertEqual(
            await le.sweep_expired_awaiting_confirmations(self.db), 1)
        self.assertEqual(self.db.loop_sessions.docs[0]["state"], "expired")


# ─── 5. Engine construction + _doc ───────────────────────────────────

class TestEngineInit(StubWorld):
    async def test_init_defaults(self):
        eng = self.engine()
        self.assertEqual(eng.state, LoopState.IDLE)
        self.assertEqual(eng.phase, "idle")
        self.assertTrue(eng.queue.empty())
        self.assertEqual(eng.context["original_request"], "do the thing")
        self.assertIsNone(eng.context["plan"])
        self.assertEqual(eng.context["files_changed"], [])
        self.assertEqual(eng.context["errors_encountered"], [])
        self.assertEqual(eng.context["self_heals_performed"], [])
        self.assertFalse(eng.used_maxx)
        self.assertFalse(eng.used_parallel_agents)
        self.assertEqual(eng.agent_count, 0)
        self.assertEqual(eng._narration_ring, [])
        self.assertIsNone(eng._pipeline_task)
        self.assertIsNone(eng.session_id)
        self.assertIsNone(eng.source_findings)
        self.assertIsNone(eng.bin_ctx)
        self.assertFalse(eng._chat_turns_saved)
        self.assertAlmostEqual(eng._started_at, __import__("time").time(),
                               delta=5)

    def test_doc_shape(self):
        eng = self.engine()
        eng.state = LoopState.EXECUTING
        eng.phase = "execute"
        doc = eng._doc()
        # NOTE (folded tree): part13 added "mode" to _doc (persisted
        # with the session so rehydration can restore it); this
        # engine was built without a mode, so it is None. p1g's "job"
        # mirror is omitted while unclaimed, so it is absent here.
        self.assertEqual(doc, {
            "loop_id": "loop_test1", "user_id": "u1",
            "project_id": None, "mode": None, "state": "executing",
            "phase": "execute", "context": eng.context})
        merged = eng._doc(extra={"last_event": {"a": 1}})
        self.assertEqual(merged["last_event"], {"a": 1})


# ─── 6. _emit / _narrate / _fail ─────────────────────────────────────

class TestEmission(StubWorld):
    async def test_emit_non_terminal(self):
        eng = self.engine()
        await eng._emit(LoopState.EXECUTING, "execute",
                        step=2, total_steps=5, message="Working")
        self.assertEqual(eng.state, LoopState.EXECUTING)
        self.assertEqual(eng.phase, "execute")
        ev = await eng.queue.get()
        self.assertEqual(ev["state"], "executing")
        self.assertEqual(ev["phase"], "execute")
        self.assertEqual(ev["message"], "Working")
        # SSE replay buffer got the event (producer-side record).
        self.assertEqual(len(self.calls["sse"]), 1)
        self.assertEqual(self.calls["sse"][0][0], "loop_test1")
        # Mongo: session upserted with last_event + loop_events row.
        doc = self.db.loop_sessions.docs[0]
        self.assertEqual(doc["state"], "executing")
        self.assertEqual(doc["last_event"]["message"], "Working")
        self.assertEqual(len(self.db.loop_events.docs), 1)
        row = self.db.loop_events.docs[0]
        self.assertEqual(row["kind"], "state_transition")
        self.assertEqual(row["loop_id"], "loop_test1")
        self.assertEqual(row["state"], "executing")
        # Non-terminal: no execution-log / funnel writes.
        self.assertEqual(self.calls["loop_beta"], [])
        self.assertEqual(self.calls["funnel"], [])

    async def test_emit_terminal_completed_logs_execution_and_funnel(self):
        self.db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        eng = self.engine()
        eng.used_maxx = True
        eng.used_parallel_agents = True
        eng.agent_count = 3
        await eng._emit(LoopState.COMPLETED, "ship",
                        message="Ship complete")
        self.assertEqual(len(self.calls["loop_beta"]), 1)
        log = self.calls["loop_beta"][0]
        self.assertEqual(log["status"], "completed")
        self.assertEqual(log["tier"], "pro")
        self.assertTrue(log["used_maxx"])
        self.assertTrue(log["used_parallel_agents"])
        self.assertEqual(log["agent_count"], 3)
        self.assertGreaterEqual(log["duration_s"], 0)
        self.assertFalse(log["worker_tape_viewed"])
        # First-ship funnel: stamped (find_one_and_update returned a doc).
        self.assertEqual(len(self.calls["funnel"]), 1)
        self.assertEqual(self.calls["funnel"][0]["event_type"],
                         "first_task_shipped")
        self.assertEqual(self.calls["funnel"][0]["user_id"], "u1")
        # No chat turns: session_id is None.
        self.assertEqual(self.db.chat_sessions.docs, [])

    async def test_emit_terminal_failed_persists_chat_turns(self):
        eng = self.engine(session_id="sess_9")
        eng._started_at = 1700000000.0
        await eng._emit(LoopState.FAILED, "verify", message="Lint failed")
        self.assertEqual(len(self.db.chat_sessions.docs), 1)
        row = self.db.chat_sessions.docs[0]
        self.assertEqual(row["session_id"], "sess_9")
        turns = row["turns"]
        self.assertEqual(len(turns), 2)
        # User turn stamped with the LOOP START time, not now.
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[0]["ts"], 1700000000.0)
        self.assertEqual(turns[0]["content"], "do the thing")
        self.assertEqual(turns[1]["role"], "assistant")
        self.assertEqual(turns[1]["provider"], "loop")
        self.assertIn("Lint failed", turns[1]["content"])
        # Fire-once: a second terminal emit does not duplicate turns.
        await eng._emit(LoopState.FAILED, "verify", message="again")
        self.assertEqual(len(self.db.chat_sessions.docs), 1)
        self.assertEqual(len(self.db.chat_sessions.docs[0]["turns"]), 2)

    async def test_narrate_emits_narration_event_without_state_change(self):
        eng = self.engine()
        eng.state = LoopState.EXECUTING
        eng.phase = "execute"
        await eng._narrate("execute", "pending", "Fix the e2b sandbox",
                           correlation_id="c1")
        ev = await eng.queue.get()
        self.assertEqual(ev["state"], "executing")  # narration never changes state
        self.assertEqual(ev["phase"], "execute")
        self.assertEqual(ev["message"], "Fix the sandbox sandbox")
        data = ev["data"]
        self.assertEqual(data["type"], "narration")
        self.assertEqual(data["tone"], "pending")
        self.assertEqual(data["narration_step"], "execute")
        self.assertEqual(data["correlation_id"], "c1")
        self.assertIsInstance(data["ts_epoch"], float)
        self.assertEqual(eng.state, LoopState.EXECUTING)

    async def test_narrate_stall_detector_auto_aborts(self):
        eng = self.engine()
        with self.assertRaises(asyncio.CancelledError):
            for _ in range(6):
                await eng._narrate("execute", "pending", "Doing the thing")
        self.assertEqual(eng.state, LoopState.FAILED)
        self.assertIn("stall_auto_abort", eng.context)
        self.assertEqual(
            eng.context["stall_auto_abort"]["repeated_sequence"],
            ["execute|pending|Doing the thing"] * 3)
        # The abort surfaced as a FAILED event with requires_user_action.
        failed = [e for e in [await eng.queue.get()
                              for _ in range(eng.queue.qsize())]
                  if e["state"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0]["requires_user_action"])

    async def test_fail_verify_carries_structured_detail(self):
        eng = self.engine()
        eng.context["verify_failed_files"] = ["a.py", "b.py"]
        eng.context["verify_last_errors"] = ["E501 line too long"]
        await eng._fail("verify", "Verify failed")
        self.assertEqual(eng.state, LoopState.FAILED)
        self.assertEqual(eng.phase, "verify")
        self.assertEqual(len(eng.context["errors_encountered"]), 1)
        self.assertEqual(eng.context["errors_encountered"][0]["phase"],
                         "verify")
        self.assertEqual(len(self.db.loop_errors.docs), 1)
        # Safety hooks: failure recorded + lock released.
        self.assertEqual(len(self.calls["record_failure"]), 1)
        self.assertEqual(self.calls["record_failure"][0][:3],
                         ("_no_project", "u1", "verify"))
        self.assertEqual(self.calls["release_lock"],
                         [("p1" if False else "_no_project", "u1",
                           "loop_test1")])
        ev = await eng.queue.get()
        self.assertEqual(ev["state"], "failed")
        self.assertTrue(ev["requires_user_action"])
        self.assertEqual(ev["data"]["kind"], "terminal_fail")
        self.assertEqual(ev["data"]["failed_files"], ["a.py", "b.py"])
        self.assertEqual(ev["data"]["errors"], ["E501 line too long"])
        self.assertEqual(ev["data"]["max_self_heals"], 2)

    async def test_fail_non_verify_has_no_file_detail(self):
        eng = self.engine()
        await eng._fail("ship", "Push failed")
        ev = await eng.queue.get()
        self.assertNotIn("failed_files", ev["data"])
        self.assertEqual(ev["data"]["reason"], "Push failed")

    def test_should_stop_matrix(self):
        eng = self.engine()
        self.assertFalse(eng._should_stop())
        eng.state = LoopState.EXECUTING
        self.assertFalse(eng._should_stop())
        eng.state = LoopState.PAUSED_FOR_USER
        self.assertTrue(eng._should_stop())
        eng.state = LoopState.COMPLETED
        self.assertTrue(eng._should_stop())
        eng.state = LoopState.FAILED
        self.assertTrue(eng._should_stop())
        eng.state = LoopState.EXECUTING
        eng._cancelled = True
        self.assertTrue(eng._should_stop())


# ─── 7. _with_budget restart semantics ───────────────────────────────

class TestWithBudget(StubWorld):
    def _patch_budget(self, phase, seconds):
        orig = le.PHASE_TIMEOUTS_S[phase]
        le.PHASE_TIMEOUTS_S[phase] = seconds
        self.addCleanup(le.PHASE_TIMEOUTS_S.__setitem__, phase, orig)

    async def test_success_runs_once(self):
        eng = self.engine()
        seen = []

        async def _ok():
            seen.append(1)

        await eng._with_budget("execute", _ok)
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(eng.state, LoopState.FAILED)

    async def test_timeout_then_success_recovers(self):
        self._patch_budget("execute", 0.05)
        eng = self.engine()
        calls = {"n": 0}

        async def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(5)  # trips the 0.05s budget

        await eng._with_budget("execute", _flaky)
        self.assertEqual(calls["n"], 2)
        self.assertNotEqual(eng.state, LoopState.FAILED)
        # The auto-restart event was emitted (SELF_HEALING, attempt 1).
        restarts = [e for e in self.db.loop_events.docs
                    if e["state"] == "self_healing"]
        self.assertEqual(len(restarts), 1)
        # Phase scratch keys were reset for the fresh attempt.
        self.assertEqual(eng.context["files_changed"], [])

    async def test_double_timeout_fails_loop(self):
        self._patch_budget("verify", 0.05)
        eng = self.engine()

        async def _slow():
            await asyncio.sleep(5)

        await eng._with_budget("verify", _slow)
        self.assertEqual(eng.state, LoopState.FAILED)
        self.assertEqual(eng.phase, "verify")
        self.assertIn("exceeded", eng.context["errors_encountered"][0]["error"])
        # MAX_PHASE_RESTARTS=1 -> exactly 2 attempts, then _fail (no 3rd).
        restarts = [e for e in self.db.loop_events.docs
                    if e["state"] == "self_healing"]
        self.assertEqual(len(restarts), 1)

    async def test_cancel_propagates_without_restart_or_fail(self):
        eng = self.engine()

        async def _cancel():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await eng._with_budget("execute", _cancel)
        self.assertNotEqual(eng.state, LoopState.FAILED)
        self.assertEqual(self.db.loop_errors.docs, [])


# ─── 8. _generate_plan (plan-LLM adapter) ────────────────────────────

class TestGeneratePlan(StubWorld):
    def _plan_module(self):
        # The module that DEFINES _generate_plan (loop_engine before
        # the split, loop_engine_plan after it). Patching the timeout
        # here works on both sides of the extraction.
        import importlib
        return importlib.import_module(le._generate_plan.__module__)

    def _set_llm(self, fn):
        sys.modules["services.llm"].call_llm_with_meta = fn

    async def test_valid_json_plan_with_profile(self):
        plan = await le._generate_plan("u1", None, "add a button")
        self.assertEqual(plan["title"], "Test plan")
        self.assertEqual(plan["files_to_change"], ["a.py"])
        self.assertEqual(plan["bullets"], ["Step one"])
        self.assertIn("_profile", plan)
        self.assertEqual(set(plan["_profile"]), {
            "graph_refresh_s", "repo_map_read_s", "llm_call_s",
            "json_parse_s", "total_s", "graph_refreshed",
            "repo_map_present"})
        self.assertFalse(plan["_profile"]["repo_map_present"])
        self.assertFalse(plan["_profile"]["graph_refreshed"])
        call = self.calls["llm"][0]
        self.assertEqual(call["review_mode"], "pro")
        self.assertEqual(call["mode"], "loop_plan")
        self.assertEqual(call["feature"], "Loop Planning")
        self.assertIn("PLAN ONLY", call["sys_msg"])

    async def test_fenced_json_is_parsed(self):
        async def _fenced(sys_msg, user_message, **kw):
            return {"content": "```json\n{\"title\": \"Fenced\", "
                    "\"files_to_change\": [], \"bullets\": [\"x\"], "
                    "\"estimated_time\": \"~2 min\"}\n```"}
        self._set_llm(_fenced)
        plan = await le._generate_plan("u1", None, "x")
        self.assertEqual(plan["title"], "Fenced")

    async def test_non_json_falls_back_to_raw_plan(self):
        async def _prose(sys_msg, user_message, **kw):
            return {"content": "Just do it in plain words"}
        self._set_llm(_prose)
        plan = await le._generate_plan("u1", None, "x")
        self.assertEqual(plan["title"], "Plan")
        self.assertEqual(plan["files_to_change"], [])
        self.assertEqual(plan["bullets"], ["Just do it in plain words"])
        self.assertEqual(plan["raw"], "Just do it in plain words")

    async def test_cost_cap_raises_distinguishable_pause(self):
        async def _capped(sys_msg, user_message, **kw):
            return {"content": "", "error_code": "COST_CAP_REACHED",
                    "error": "Monthly task budget reached."}
        self._set_llm(_capped)
        with self.assertRaises(le._CostCapPaused) as ctx:
            await le._generate_plan("u1", None, "x")
        self.assertEqual(ctx.exception.message,
                         "Monthly task budget reached.")

    async def test_llm_timeout_raises_runtime_error(self):
        mod = self._plan_module()
        orig = mod.PLAN_LLM_TIMEOUT_S
        mod.PLAN_LLM_TIMEOUT_S = 0.05
        self.addCleanup(setattr, mod, "PLAN_LLM_TIMEOUT_S", orig)

        async def _slow(sys_msg, user_message, **kw):
            await asyncio.sleep(5)
            return {"content": "{}"}
        self._set_llm(_slow)
        with self.assertRaises(RuntimeError) as ctx:
            await le._generate_plan("u1", None, "x")
        self.assertIn("timed out after 0.05s", str(ctx.exception))

    async def test_repo_map_injected_when_present(self):
        self.db.project_graphs.docs.append(
            {"project_id": "p1", "user_id": "u1", "built_at": 0})
        # get_db returns the fake db; no project row -> no rebuild.
        sys.modules["cto_services.db"].get_db = lambda: self.db
        sys.modules["services.repo_map"].build_repo_map = (
            lambda db, pid, uid: _coro({
                "has_map": True, "file_count": 3, "char_count": 100,
                "map_text": "a.py [x] · symbols: f"}))
        plan = await le._generate_plan("u1", "p1", "x")
        call = self.calls["llm"][0]
        self.assertIn("COMPACT REPO MAP (3 files, 100 chars)",
                      call["sys_msg"])
        self.assertIn("a.py [x]", call["sys_msg"])
        self.assertTrue(plan["_profile"]["repo_map_present"])
        self.assertFalse(plan["_profile"]["graph_refreshed"])


def _coro(value):
    async def _f(*args, **kwargs):
        return value
    return _f()


# ─── 9. _do_verify global self-heal cap ─────────────────────────────

class TestVerifyCap(StubWorld):
    async def test_global_heal_cap_halts_loop_without_healing(self):
        eng = self.engine()
        eng.context["submitted_files"] = [
            {"path": "a.py", "content": "x = 1\n"}]
        eng.context["total_heal_attempts"] = 2  # cap already consumed

        async def _failing(file_objs):
            return {"ok": False,
                    "results": [{"path": "a.py", "ok": False,
                                 "stderr": "E501"}],
                    "errors": ["a.py: E501"]}
        sys.modules["services.loop_verify"].verify_files = _failing

        await eng._do_verify()
        # Halted at exactly the cap: FAILED, not paused, not retried.
        self.assertEqual(eng.state, LoopState.FAILED)
        self.assertEqual(eng.phase, "verify")
        self.assertIn("Self-heal exhausted globally",
                      eng.context["errors_encountered"][0]["error"])
        # verify_files ran exactly once (initial pass); no heal round.
        self.assertEqual(eng.context["total_heal_attempts"], 2)

    async def test_verify_pass_short_circuits_with_no_heal(self):
        eng = self.engine()
        eng.context["submitted_files"] = [
            {"path": "a.py", "content": "x = 1\n"}]
        await eng._do_verify()
        self.assertEqual(eng.state, LoopState.VERIFYING)
        self.assertTrue(eng.context["verification_results"]["ok"])
        self.assertEqual(eng.context.get("total_heal_attempts", 0), 0)
        self.assertEqual(len(self.calls["audit"]), 1)
        self.assertEqual(self.calls["audit"][0]["kind"], "vanguard")


# ─── 9b. Wallet hold settle/release on terminal persist ────────────

class TestWalletHook(StubWorld):
    async def test_terminal_failed_finalizes_hold_not_ok(self):
        eng = self.engine()
        await eng._fail("ship", "Push failed")
        # _fail() persists once AND _emit(FAILED) persists again; the
        # wallet hook is idempotent by design (helpers docstring), so
        # both calls land with the same not-ok payload.
        self.assertEqual(len(self.calls["wallet"]), 2)
        for hold in self.calls["wallet"]:
            self.assertEqual(hold["action"], "loop_run")
            self.assertEqual(hold["run_id"], "loop_test1")
            self.assertFalse(hold["ok"])
            self.assertEqual(hold["reason"], "loop_state:failed")

    async def test_terminal_completed_finalizes_hold_ok(self):
        eng = self.engine()
        await eng._emit(LoopState.COMPLETED, "ship", message="done")
        self.assertEqual(len(self.calls["wallet"]), 1)
        self.assertTrue(self.calls["wallet"][0]["ok"])
        self.assertEqual(self.calls["wallet"][0]["reason"],
                         "loop_state:completed")

    async def test_non_terminal_persist_does_not_touch_wallet(self):
        eng = self.engine()
        await eng._emit(LoopState.EXECUTING, "execute", message="go")
        self.assertEqual(self.calls["wallet"], [])


# ─── 10. start() plan-phase happy path ──────────────────────────────

class TestStart(StubWorld):
    async def test_start_plans_and_awaits_confirmation(self):
        plan = {"title": "Add button", "files_to_change": ["a.py"],
                "bullets": ["Add it"], "estimated_time": "~1 min",
                "_profile": {"graph_refresh_s": 0.0, "repo_map_read_s": 0.0,
                             "llm_call_s": 0.1, "json_parse_s": 0.0,
                             "total_s": 0.1, "graph_refreshed": False,
                             "repo_map_present": False}}
        calls = []

        async def _fake_plan(user_id, project_id, user_message):
            calls.append((user_id, project_id, user_message))
            return dict(plan)
        orig = le._generate_plan
        le._generate_plan = _fake_plan
        self.addCleanup(setattr, le, "_generate_plan", orig)

        eng = self.engine()
        events = [e async for e in eng.start()]
        self.assertEqual(calls, [("u1", None, "do the thing")])
        self.assertEqual(eng.state, LoopState.AWAITING_CONFIRMATION)
        self.assertEqual(eng.phase, "plan")
        self.assertEqual(eng.context["plan"]["title"], "Add button")
        # First event: planning; last: awaiting confirmation w/ plan.
        self.assertEqual(events[0]["state"], "planning")
        self.assertEqual(events[-1]["state"], "awaiting_confirmation")
        self.assertTrue(events[-1]["requires_user_action"])
        self.assertEqual(events[-1]["data"]["plan"]["title"], "Add button")
        # Latency profile persisted to loop_run_log (stripped from plan).
        self.assertNotIn("_profile", eng.context["plan"])
        prof_rows = [d for d in self.db.loop_run_log.docs
                     if d.get("kind") == "plan_latency_profile"]
        self.assertEqual(len(prof_rows), 1)
        self.assertEqual(prof_rows[0]["profile"]["llm_call_s"], 0.1)
        # Plan persisted (24h TTL row) + task spec frozen.
        self.assertEqual(len(self.db.loop_plans.docs), 1)
        self.assertEqual(self.db.loop_plans.docs[0]["plan"]["title"],
                         "Add button")
        self.assertEqual(len(self.calls["freeze"]), 1)

    async def test_start_cost_cap_pauses_instead_of_failing(self):
        async def _paused_plan(user_id, project_id, user_message):
            raise le._CostCapPaused("Monthly task budget reached.")
        orig = le._generate_plan
        le._generate_plan = _paused_plan
        self.addCleanup(setattr, le, "_generate_plan", orig)

        eng = self.engine()
        events = [e async for e in eng.start()]
        self.assertEqual(eng.state, LoopState.PAUSED_FOR_USER)
        self.assertEqual(eng.context["error_code"], "COST_CAP_REACHED")
        self.assertEqual(events[-1]["state"], "paused_for_user")
        self.assertTrue(events[-1]["requires_user_action"])


if __name__ == "__main__":
    unittest.main()
