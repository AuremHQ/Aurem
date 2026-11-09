"""tests/test_regression_iter2026_47_part2b_r4_lock_scope_and_bounding.py

Part 2b R4 — two independent problems in services/fix_batches.py:

  1. `apply_finding_fix`/`apply_file_fixes` used to hold the SAME
     per-(user, project) lock across the ENTIRE fetch -> LLM -> commit
     pipeline. The LLM call is the slow part (seconds), so a 2nd fix
     for a DIFFERENT file in the same project queued behind the 1st
     fix's full LLM latency even though they share no real resource
     conflict until the (fast) Git write. Fixed by narrowing the lock
     to just the batch-resolve step and the commit step, with an
     optimistic-concurrency re-check in between (covered by test files
     41/43's drift tests) to preserve the same-file guarantee.
  2. `_batch_locks` grew by one entry per (user_id, project_id) pair
     EVER seen, for the life of the process — an unbounded dict is a
     slow memory leak on a long-running pod. Fixed with a bounded,
     best-effort LRU eviction that NEVER evicts a currently-held lock.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import services.fix_batches as fb

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_locks():
    fb._batch_locks.clear()
    yield
    fb._batch_locks.clear()


class TestBoundedBatchLocks:
    def test_lock_map_never_exceeds_the_max_once_unlocked_entries_exist(self):
        for i in range(fb._MAX_BATCH_LOCKS + 50):
            fb._lock_for(f"user_{i}", "p1")
        assert len(fb._batch_locks) <= fb._MAX_BATCH_LOCKS

    async def test_eviction_never_removes_a_currently_held_lock(self):
        """Fill the map with (_MAX_BATCH_LOCKS - 1) unlocked entries,
        then acquire ONE more lock and HOLD it while triggering
        eviction pressure — that held lock must survive."""
        for i in range(fb._MAX_BATCH_LOCKS - 1):
            fb._lock_for(f"user_{i}", "p1")

        held_lock = fb._lock_for("held_user", "p1")
        async with held_lock:
            # Push well past the cap while the lock above is held.
            for i in range(fb._MAX_BATCH_LOCKS - 1, fb._MAX_BATCH_LOCKS + 100):
                fb._lock_for(f"user_{i}", "p1")
            # The held lock must still be the SAME object, still held,
            # never evicted out from under the in-flight caller.
            assert fb._batch_locks.get(("held_user", "p1")) is held_lock
            assert held_lock.locked()

    def test_reusing_the_same_key_returns_the_same_lock_object(self):
        l1 = fb._lock_for("u1", "p1")
        l2 = fb._lock_for("u1", "p1")
        assert l1 is l2


class TestLockScopeNarrowedAroundSlowWork:
    async def test_two_different_file_fixes_overlap_during_the_slow_llm_call(self, monkeypatch):
        """R4's actual claim: the lock is NOT held across the LLM
        call. Two concurrent apply_finding_fix calls for the SAME
        project but DIFFERENT files must both be mid-flight (inside
        their slow, mocked LLM call) AT THE SAME TIME — proving
        neither queued behind the other's full fetch->LLM latency."""
        import services.finding_fix_applier as ffa
        import services.loop_safety as ls

        async def _get_token(proj):
            return "ghp_tok", None, None
        monkeypatch.setattr("services.pat_vault.get_repo_token_or_error", _get_token)

        async def _create_branch(**kw):
            return True, None

        async def _open_pr(**kw):
            return {"ok": True, "pr_url": "https://github.com/o/r/pull/1",
                    "pr_number": 1, "reused": False, "error": None}

        async def _add_label(**kw):
            return True, None

        async def _update_body(**kw):
            return True, None

        async def _get_status(**kw):
            return {"ok": True, "merged": False, "state": "open", "merge_commit_sha": None}

        monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)
        monkeypatch.setattr(ls, "open_or_reuse_pr", _open_pr)
        monkeypatch.setattr(ls, "add_pr_label", _add_label)
        monkeypatch.setattr(ls, "update_pr_body", _update_body)
        monkeypatch.setattr(ls, "get_pr_status", _get_status)

        async def _fetch(owner, repo, branch, path, token):
            return f"orig {path}", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        in_flight = set()
        overlap_observed = {"seen": False}

        async def _slow_llm(*, path, current_content, finding, user_id, db):
            in_flight.add(path)
            if len(in_flight) >= 2:
                overlap_observed["seen"] = True
            await asyncio.sleep(0.15)
            in_flight.discard(path)
            return current_content + "\nFIXED", None
        monkeypatch.setattr(ffa, "_generate_patched_content", _slow_llm)
        monkeypatch.setattr(ffa, "_finding_still_present",
                             lambda patched, path, finding: "FIXED" not in patched)

        async def _commit(**kw):
            return {"sha": "s1", "full_sha": "s1" * 20, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        async def _get_branch_head(owner, repo, branch, token):
            return {"sha": "parent_sha"}
        monkeypatch.setattr("services.github_api_writer._get_branch_head", _get_branch_head)

        class _FakeColl:
            def __init__(self, docs=None):
                self.docs = docs if docs is not None else []

            async def find_one(self, filt, proj=None):
                for d in self.docs:
                    if all(d.get(k) == v for k, v in filt.items()):
                        return dict(d)
                return None

            async def insert_one(self, row):
                self.docs.append(dict(row))

            async def update_one(self, filt, update):
                return

            async def update_many(self, filt, update):
                return

            def find(self, filt, proj=None):
                class _Cur:
                    async def to_list(self, length=None):
                        return []
                return _Cur()

        class _FakeFixBatches(_FakeColl):
            async def find_one_and_update(self, filt, update, return_document=False):
                for d in self.docs:
                    if all(d.get(k) == v for k, v in filt.items()):
                        for k, v in update.get("$inc", {}).items():
                            d[k] = d.get(k, 0) + v
                        for k, v in update.get("$set", {}).items():
                            d[k] = v
                        return dict(d)
                return None

        class _FakeDB:
            def __init__(self):
                self.cto_projects = _FakeColl([{
                    "project_id": "p1", "user_id": "u1",
                    "github_owner": "o", "github_repo": "r",
                    "github_branch": "main",
                }])
                self.finding_fixes = _FakeColl()
                self.fix_batches = _FakeFixBatches()
                self.cto_open_findings = _FakeColl()
                self.fixed_findings = _FakeColl()

            def __getitem__(self, name):
                return getattr(self, name)

        db = _FakeDB()
        t0 = time.monotonic()
        results = await asyncio.gather(
            ffa.apply_finding_fix(
                db=db, user={"user_id": "u1"}, project_id="p1",
                finding={"rule_id": "r1", "file": "x.py", "line": 1},
            ),
            ffa.apply_finding_fix(
                db=db, user={"user_id": "u1"}, project_id="p1",
                finding={"rule_id": "r2", "file": "y.py", "line": 1},
            ),
        )
        elapsed = time.monotonic() - t0

        assert all(r["ok"] for r in results)
        assert overlap_observed["seen"] is True, (
            "both LLM calls must overlap — the lock must NOT serialize "
            "the slow fetch->LLM phase for different files"
        )
        # If the lock had serialized the two 0.15s LLM calls, this
        # would take >=0.30s; overlapping finishes close to 0.15s.
        assert elapsed < 0.28, f"took {elapsed:.3f}s — looks serialized, not overlapped"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
