"""tests/test_regression_iter2026_29_brain_part4_f2_wiring.py

ORA Brain Part 4, F2 — the learning loop wiring: task_id/project_id
now flow from services.loop_token_ledger.current_surface_context()
into every brain_calls row; attach_outcome_for_task finds every row
for a task in one shot; the precedence rule (first positive sticks,
a later negative ALWAYS overrides and demotes); cross-user isolation
on both call_id- and task_id-scoped outcome attach; and the actual
hook wiring in the fix/rollback/webhook call sites.
"""
from __future__ import annotations

import asyncio

import pytest

import services.brain_gateway as bg
import services.loop_token_ledger as ltl
import services.project_memory as pm

pytestmark = pytest.mark.asyncio


# ───────────────────────────── fake DB double ────────────────────────────
class _UpdateResult:
    def __init__(self, matched_count, modified_count=None):
        self.matched_count = matched_count
        self.modified_count = modified_count if modified_count is not None else matched_count


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        v = self._rows[self._i]
        self._i += 1
        return v


class _FakeBrainCalls:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, r, filt):
        for k, v in filt.items():
            if isinstance(v, dict) and "$in" in v:
                if r.get(k) not in v["$in"]:
                    return False
            elif r.get(k) != v:
                return False
        return True

    async def insert_one(self, row):
        self.rows.append(dict(row))

    def find(self, filt, proj=None):
        # dict(r) — a REAL Mongo driver's find() returns independent
        # document snapshots, not live references into the same
        # in-memory store. Returning live refs here would let a
        # LATER update_one/update_many mutate an "already fetched"
        # snapshot out from under code that fetched it specifically
        # to capture pre-update state (e.g. _demote_revoked_rows).
        return _FakeCursor([dict(r) for r in self.rows if self._match(r, filt)])

    async def find_one(self, filt, proj=None):
        for r in self.rows:
            if self._match(r, filt):
                return r
        return None

    async def update_one(self, filt, update):
        for r in self.rows:
            if self._match(r, filt):
                r.update(update.get("$set", {}))
                return _UpdateResult(1)
        return _UpdateResult(0)

    async def update_many(self, filt, update):
        n = 0
        for r in self.rows:
            if self._match(r, filt):
                r.update(update.get("$set", {}))
                n += 1
        return _UpdateResult(n)


class _FakeProjectMemory:
    def __init__(self):
        self.docs: dict = {}

    async def update_one(self, filt, update, upsert=False):
        key = (filt.get("user_id"), filt.get("project_id"))
        doc = self.docs.setdefault(key, {})
        for k, v in update.get("$set", {}).items():
            doc[k] = v
        for k, v in update.get("$pull", {}).items():
            if isinstance(doc.get(k), list) and v in doc[k]:
                doc[k].remove(v)


class _FakeDB:
    def __init__(self):
        self.brain_calls = _FakeBrainCalls()
        self.project_memory = _FakeProjectMemory()
        self.cto_projects = _FakeCtoProjects()

    def __getitem__(self, name):
        return getattr(self, name)


class _FakeCtoProjects:
    def __init__(self):
        self.owned: set[tuple] = set()

    async def find_one(self, filt, proj=None):
        key = (filt.get("user_id"), filt.get("project_id"))
        return {"user_id": key[0], "project_id": key[1]} if key in self.owned else None


def _own(db, user_id, project_id):
    db.cto_projects.owned.add((user_id, project_id))


@pytest.fixture(autouse=True)
async def _reset_gateway_state(monkeypatch):
    monkeypatch.setattr(bg, "_write_queue", None)
    monkeypatch.setattr(bg, "_worker_task", None)
    monkeypatch.setattr(bg, "_dropped_count", 0)
    yield
    task = bg._worker_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ─────────────────── task_id/project_id now flow correctly ───────────────
class TestCurrentSurfaceContextWiring:
    async def test_tag_request_surface_now_accepts_task_id(self):
        """F2's named bug: tag_request_surface() had no task_id kwarg
        at all — any call site passing one raised TypeError."""
        ltl.tag_request_surface("test_surface", project_id="p1", task_id="t1")
        ctx = ltl.current_surface_context()
        assert ctx["project_id"] == "p1"
        assert ctx["task_id"] == "t1"

    async def test_context_survives_across_an_await_point(self):
        """Sanity: the ambient context set by llm_surface_context is
        still correct after an `await`, inside the SAME task — the
        exact shape the 3 real entry points rely on (they read
        current_surface_context() after awaiting the LLM call)."""
        async with ltl.llm_surface_context(project_id="proj-42", task_id="task-99"):
            await asyncio.sleep(0)
            ctx = ltl.current_surface_context()
            assert ctx["project_id"] == "proj-42"
            assert ctx["task_id"] == "task-99"

    async def test_call_llm_with_meta_passes_ambient_project_and_task_id_to_record_call(self, monkeypatch):
        import services.llm as sl
        import services.llm._meta as meta_mod
        captured = {}

        async def _fake_record_call(**kwargs):
            captured.update(kwargs)
            return "bc_fake"
        monkeypatch.setattr(bg, "record_call", _fake_record_call)

        async def _fake_impl(system, user, max_tokens=1500, mode="chat",
                              user_id=None, review_mode=None, step_hook=None,
                              feature=None):
            return {"ok": True, "content": "x", "model": "m"}
        monkeypatch.setattr(meta_mod, "_call_llm_with_meta_impl", _fake_impl)

        async with ltl.llm_surface_context(project_id="proj-7", task_id="task-7"):
            await sl.call_llm_with_meta("sys", "usr", mode="chat", user_id="u1")

        assert captured.get("project_id") == "proj-7"
        assert captured.get("task_id") == "task-7"


# ──────────────────────── attach_outcome_for_task ────────────────────────
class TestAttachOutcomeForTask:
    async def test_attaches_to_every_row_for_the_task(self):
        db = _FakeDB()
        db.brain_calls.rows.extend([
            {"call_id": "bc_1", "task_id": "t1", "user_id": "u1", "outcome": None},
            {"call_id": "bc_2", "task_id": "t1", "user_id": "u1", "outcome": None},
            {"call_id": "bc_3", "task_id": "OTHER", "user_id": "u1", "outcome": None},
        ])
        n = await bg.attach_outcome_for_task(db, "t1", "tests_passed", user_id="u1")
        assert n == 2
        assert db.brain_calls.rows[0]["outcome"] == "tests_passed"
        assert db.brain_calls.rows[1]["outcome"] == "tests_passed"
        assert db.brain_calls.rows[2]["outcome"] is None

    async def test_cross_user_isolation_by_task_id(self):
        """User A cannot attach an outcome to user B's task_id, even
        knowing the exact task_id string."""
        db = _FakeDB()
        db.brain_calls.rows.append(
            {"call_id": "bc_1", "task_id": "shared-task-name", "user_id": "userB", "outcome": None},
        )
        n = await bg.attach_outcome_for_task(db, "shared-task-name", "tests_passed", user_id="userA")
        assert n == 0
        assert db.brain_calls.rows[0]["outcome"] is None

    async def test_operator_injection_in_task_id_rejected(self):
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "task_id": "t1", "user_id": "u1", "outcome": None})
        n = await bg.attach_outcome_for_task(db, {"$ne": None}, "tests_passed", user_id="u1")
        assert n == 0

    async def test_outcome_allow_list_only(self):
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "task_id": "t1", "user_id": "u1", "outcome": None})
        n = await bg.attach_outcome_for_task(db, "t1", "the model says 10/10", user_id="u1")
        assert n == 0


# ──────────────── precedence rule: negative always overrides ─────────────
class TestPrecedenceRule:
    async def test_negative_after_positive_overrides(self):
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "task_id": "t1", "user_id": "u1", "outcome": None})
        await bg.attach_outcome_for_task(db, "t1", "pr_merged", user_id="u1")
        assert db.brain_calls.rows[0]["outcome"] == "pr_merged"
        await bg.attach_outcome_for_task(db, "t1", "rolled_back", user_id="u1")
        assert db.brain_calls.rows[0]["outcome"] == "rolled_back"

    async def test_positive_after_negative_never_overrides(self):
        """Negative landed first — a LATER positive must never
        resurrect it."""
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "task_id": "t1", "user_id": "u1", "outcome": None})
        await bg.attach_outcome_for_task(db, "t1", "rolled_back", user_id="u1")
        await bg.attach_outcome_for_task(db, "t1", "pr_merged", user_id="u1")
        assert db.brain_calls.rows[0]["outcome"] == "rolled_back"

    async def test_negative_wins_regardless_of_race_order(self):
        """asyncio.gather — a positive and a negative racing for the
        SAME task must deterministically land on the negative
        outcome no matter which coroutine the event loop runs
        first."""
        for _ in range(10):  # repeat — interleaving order isn't fixed
            db = _FakeDB()
            db.brain_calls.rows.append(
                {"call_id": "bc_1", "task_id": "t1", "user_id": "u1", "outcome": None},
            )
            await asyncio.gather(
                bg.attach_outcome_for_task(db, "t1", "pr_merged", user_id="u1"),
                bg.attach_outcome_for_task(db, "t1", "rolled_back", user_id="u1"),
            )
            assert db.brain_calls.rows[0]["outcome"] == "rolled_back"

    async def test_second_positive_after_first_positive_is_noop(self):
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "task_id": "t1", "user_id": "u1", "outcome": None})
        await bg.attach_outcome_for_task(db, "t1", "tests_passed", user_id="u1")
        n = await bg.attach_outcome_for_task(db, "t1", "user_approved", user_id="u1")
        assert n == 0
        assert db.brain_calls.rows[0]["outcome"] == "tests_passed"


# ─────────────────── demotion: revoke promotion + strip the note ─────────
class TestDemotionOnNegativeOutcome:
    async def test_merged_then_rolled_back_is_revoked_everywhere(self, monkeypatch):
        db = _FakeDB()
        _own(db, "u1", "p1")
        db.brain_calls.rows.append({
            "call_id": "bc_1", "task_id": "t1", "user_id": "u1",
            "project_id": "p1", "task_type": "fix", "outcome": None,
            "promoted": "raw",
        })

        # 1. Merge — positive outcome.
        await bg.attach_outcome_for_task(db, "t1", "pr_merged", user_id="u1")
        assert db.brain_calls.rows[0]["outcome"] == "pr_merged"

        # 2. Promote to candidate (normally fired as a background
        # task by _fire_promotion — called directly here for a
        # deterministic test).
        n = await bg.promote_to_candidate(db, "u1")
        assert n == 1
        assert db.brain_calls.rows[0]["promoted"] == "candidate"
        assert "fix succeeded (pr_merged)" in db.project_memory.docs[("u1", "p1")]["fixes_that_worked"]

        # 3. Export sees it.
        draft = await bg.export_anonymized_global_draft(db)
        assert len(draft) == 1

        # 4. Rollback — negative outcome MUST demote.
        await bg.attach_outcome_for_task(db, "t1", "rolled_back", user_id="u1")
        assert db.brain_calls.rows[0]["outcome"] == "rolled_back"
        assert db.brain_calls.rows[0]["promoted"] == "revoked"

        # 5. Note is gone from project_memory.
        assert "fix succeeded (pr_merged)" not in db.project_memory.docs[("u1", "p1")].get("fixes_that_worked", [])

        # 6. Export no longer sees it (only reads promoted="candidate").
        draft_after = await bg.export_anonymized_global_draft(db)
        assert len(draft_after) == 0

    async def test_demotion_is_a_noop_if_never_promoted(self):
        """A row that got a positive outcome but was never actually
        promoted (e.g. no project_id, or promote_to_candidate never
        ran) demotes safely with no project_memory side effect."""
        db = _FakeDB()
        db.brain_calls.rows.append({
            "call_id": "bc_1", "task_id": "t1", "user_id": "u1",
            "project_id": "p1", "task_type": "fix", "outcome": "pr_merged",
            "promoted": "raw",
        })
        await bg.attach_outcome_for_task(db, "t1", "rolled_back", user_id="u1")
        assert db.brain_calls.rows[0]["outcome"] == "rolled_back"
        assert db.brain_calls.rows[0]["promoted"] == "raw"  # untouched — nothing to revoke


# ──────────────── hook wiring — source-level, each named call site ───────
class TestHookWiringPresent:
    """Behavioral tests above prove attach_outcome_for_task/precedence
    work; these confirm the actual PRODUCTION call sites the reviewer
    named really call it (not just that the function exists in
    isolation)."""

    @staticmethod
    def _read(rel):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")

    def test_fix_pipeline_failure_and_success_hooks_wired(self):
        src = self._read("routers/fix_pipeline.py")
        assert src.count("attach_outcome_for_task") >= 2
        assert '"tests_failed"' in src
        assert '"verification_passed"' in src

    def test_codebase_health_failure_hook_wired(self):
        src = self._read("routers/codebase_health.py")
        assert "attach_outcome_for_task" in src
        assert '"tests_failed"' in src

    def test_security_scan_failure_hook_wired(self):
        src = self._read("routers/security_scan.py")
        assert "attach_outcome_for_task" in src
        assert '"tests_failed"' in src

    def test_loop_safety_pr_merged_hook_wired(self):
        src = self._read("services/loop_safety.py")
        assert "attach_outcome_for_task" in src
        assert '"pr_merged"' in src
        # Must not weaken the "called after signature verification"
        # contract documented on dispatch_pull_request_webhook.
        assert "AFTER signature" in src or "already-trusted payload" in src

    def test_loop_rollback_hook_wired(self):
        src = self._read("services/loop_rollback.py")
        assert "attach_outcome_for_task" in src
        assert '"rolled_back"' in src

    def test_rollback_two_phase_hook_wired(self):
        src = self._read("services/rollback_two_phase.py")
        assert "attach_outcome_for_task" in src
        assert '"rolled_back"' in src


# ─────────────────── F5 — record_wrap (side-door helper) ─────────────────
class TestRecordWrap:
    async def test_returns_the_wrapped_coroutines_result_unchanged(self, monkeypatch):
        monkeypatch.setattr(bg, "record_call", _NoopRecordCall())

        async def _inner():
            return {"content": "real reply", "extra": 123}
        result = await bg.record_wrap(_inner(), user_id="u1", surface="test")
        assert result == {"content": "real reply", "extra": 123}

    async def test_propagates_the_wrapped_coroutines_exception_unchanged(self, monkeypatch):
        monkeypatch.setattr(bg, "record_call", _NoopRecordCall())

        async def _boom():
            raise ValueError("upstream provider exploded")
        with pytest.raises(ValueError, match="upstream provider exploded"):
            await bg.record_wrap(_boom(), user_id="u1", surface="test")

    async def test_records_exactly_once_on_success(self, monkeypatch):
        calls = []

        async def _fake_record_call(**kwargs):
            calls.append(kwargs)
            return "bc_x"
        monkeypatch.setattr(bg, "record_call", _fake_record_call)

        async def _inner():
            return "ok"
        await bg.record_wrap(
            _inner(), user_id="u1", project_id="p1", task_type="agent",
            model="m", surface="agents",
        )
        assert len(calls) == 1
        assert calls[0]["user_id"] == "u1"
        assert calls[0]["project_id"] == "p1"
        assert calls[0]["surface"] == "agents"

    async def test_still_records_on_failure(self, monkeypatch):
        calls = []

        async def _fake_record_call(**kwargs):
            calls.append(kwargs)
            return "bc_x"
        monkeypatch.setattr(bg, "record_call", _fake_record_call)

        async def _boom():
            raise RuntimeError("timeout")
        with pytest.raises(RuntimeError):
            await bg.record_wrap(_boom(), user_id="u1", surface="agents")
        assert len(calls) == 1

    async def test_recording_failure_never_breaks_the_real_result(self, monkeypatch):
        """Fail-open: even if record_call itself raises, the wrapped
        call's own result/exception must still come through clean."""
        async def _broken_record_call(**kwargs):
            raise RuntimeError("gateway is on fire")
        monkeypatch.setattr(bg, "record_call", _broken_record_call)

        async def _inner():
            return "still works"
        result = await bg.record_wrap(_inner(), user_id="u1", surface="agents")
        assert result == "still works"


class _NoopRecordCall:
    async def __call__(self, **kwargs):
        return "bc_noop"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
