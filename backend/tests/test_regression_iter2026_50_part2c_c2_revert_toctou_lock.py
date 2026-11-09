"""tests/test_regression_iter2026_50_part2c_c2_revert_toctou_lock.py

Part 2c C2 [Med] — `_revert_single_fix_on_batch` (and `revert_batch`)
do NOT take `fix_batches._lock_for(user_id, project_id)`, the SAME
lock `apply_finding_fix`/`apply_file_fixes` take around their own
fetch->compare->commit critical section. Revert's own fetch-compare-
commit sequence is therefore a classic TOCTOU: it reads the branch's
CURRENT content (the compare), and if that matches what it expects,
writes a NEW commit (the revert) — but nothing stops a DIFFERENT fix
landing on the SAME file in the gap between that read and that write.
The result is a lost update: the concurrent fix's new commit is
silently overwritten by the revert's parent-content commit, with no
error raised to either caller.

Fix: `_revert_single_fix_on_batch` acquires the SAME per-(user,
project) lock around its fetch-compare-commit section (no LLM call
inside the lock — this is a short, Git-only critical section). If the
file changed since the fix's own `commit_sha` even AFTER taking the
lock (i.e. the content fetched at lock-acquisition time already
differs), it returns a clean, retryable error instead of overwriting.
"""
from __future__ import annotations

import asyncio

import pytest

import services.finding_fix_applier as ffa
import services.fix_batches as fb

pytestmark = pytest.mark.asyncio


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs if docs is not None else []

    async def find_one(self, filt, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                return dict(d)
        return None

    async def insert_one(self, row):
        self.docs.append(dict(row))

    async def update_one(self, filt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                d.update(update.get("$set", {}))
                return

    async def update_many(self, filt, update):
        n = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                d.update(update.get("$set", {}))
                n += 1
        return n

    def find(self, filt, proj=None):
        matched = [dict(d) for d in self.docs
                   if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict))]

        class _Cur:
            def __init__(self, items):
                self.items = items

            async def to_list(self, length=None):
                return list(self.items)[: (length if length is not None else len(self.items))]
        return _Cur(matched)

    async def delete_many(self, filt):
        before = len(self.docs)
        self.docs = [d for d in self.docs
                     if not all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict))]
        return before - len(self.docs)


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
        self.fix_batches = _FakeFixBatches([{
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "base_branch": "main",
            "status": "open", "fix_count": 1, "pr_number": 1,
            "pr_url": "https://github.com/o/r/pull/1",
        }])
        self.cto_open_findings = _FakeColl()
        self.fixed_findings = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture(autouse=True)
def _reset_locks():
    fb._batch_locks.clear()
    yield
    fb._batch_locks.clear()


def _mock_pat(monkeypatch):
    async def _get_token(proj):
        return "ghp_tok", None, None
    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error", _get_token)


def _mock_loop_safety(monkeypatch):
    import services.loop_safety as ls

    async def _get_status(**kw):
        return {"ok": True, "merged": False, "state": "open", "merge_commit_sha": None}
    monkeypatch.setattr(ls, "get_pr_status", _get_status)


class TestC2RevertToctou:
    async def test_concurrent_fix_and_revert_on_same_file_never_silently_loses_an_update(self, monkeypatch):
        """A NEW fix on shared.py races a revert of an OLDER fix on
        the SAME file. The new fix's LLM call has real latency (a
        genuine `await`, unlike a fake-DB round-trip) — exactly the
        window where, pre-fix, the revert's fetch-compare-commit
        (itself a TOCTOU, no lock) could land AND silently overwrite
        the new fix with no error at all (proven failing against the
        pre-fix code: both calls returned ok=True, final content had
        no trace of "NEW_FIX_APPLIED").

        Post-fix invariant (this must hold under EITHER interleaving
        the real scheduler picks): the new fix's work is NEVER
        silently discarded. Either it's still present in the final
        content (the lock serialized the two commits and
        apply_finding_fix's own Part 2b R4 optimistic-regenerate
        re-applied it after the revert), or the revert itself failed
        with an explicit, retryable error instead of overwriting."""
        _mock_pat(monkeypatch)
        _mock_loop_safety(monkeypatch)
        db = _FakeDB()
        branch = "aurem/fixes-aaaaaaaa"
        parent_sha, after_sha = "parent_old", "after_old"

        db.finding_fixes.docs.append({
            "fix_id": "fx_old", "user_id": "u1", "project_id": "p1",
            "batch_id": "fb_1", "file": "shared.py", "rule_id": "rule_old",
            "branch": branch, "parent_commit_sha": parent_sha, "commit_sha": after_sha,
        })

        branch_content = {
            parent_sha: "BEFORE_OLD_FIX\n",
            after_sha: "AFTER_OLD_FIX\n",
            branch: "AFTER_OLD_FIX\n",
        }

        async def _fetch(owner, repo, ref, path, token):
            return branch_content[ref], None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        async def _get_branch_head(owner, repo, br, token):
            return {"sha": "whatever_head"}
        monkeypatch.setattr("services.github_api_writer._get_branch_head", _get_branch_head)

        async def _llm_with_real_delay(*, path, current_content, finding, user_id, db):
            # A real LLM call has genuine latency — this is the exact
            # window C2 says the revert's own fetch-compare could
            # land inside, uncontested, pre-fix.
            await asyncio.sleep(0.05)
            return current_content + "NEW_FIX_APPLIED\n", None
        monkeypatch.setattr(ffa, "_generate_patched_content", _llm_with_real_delay)
        monkeypatch.setattr(ffa, "_finding_still_present",
                             lambda patched, path, finding: "NEW_FIX_APPLIED" not in patched)

        commits_landed = []

        async def _commit(*, owner, repo, branch: str, token, files, commit_message,
                           author_name, author_email, progress):
            path = next(iter(files))
            new_content = files[path]
            branch_content[branch] = new_content
            commits_landed.append(new_content)
            return {"sha": "s", "full_sha": "s" * 40, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        async def _run_new_fix():
            return await ffa.apply_finding_fix(
                db=db, user={"user_id": "u1"}, project_id="p1",
                finding={"rule_id": "rule_new", "file": "shared.py", "line": 1},
            )

        async def _run_revert():
            return await ffa.revert_finding_fix(
                db, fix_id="fx_old", token="tok", user_id="u1", project_id="p1",
            )

        new_res, revert_res = await asyncio.gather(_run_new_fix(), _run_revert())

        final_content = branch_content[branch]
        if revert_res.get("ok") and new_res.get("ok"):
            # Both succeeded — the new fix's work MUST be present
            # somewhere in the final content, however the two
            # commits were ordered (lock-serialized, so one always
            # fully completes before the other's critical section
            # starts; apply_finding_fix's own optimistic-concurrency
            # regenerate — Part 2b R4 — is what re-applies it if the
            # revert happened to land first).
            assert "NEW_FIX_APPLIED" in final_content, (
                "C2 FIX REQUIRED: both calls reported ok=True but the "
                "new fix's work vanished from the final content — a "
                "silent lost update. commits_landed="
                f"{commits_landed!r} final={final_content!r}"
            )
        elif revert_res.get("ok") and not new_res.get("ok"):
            # The lock forced the new fix to detect the revert's
            # drift and fail cleanly (retryable) instead of racing —
            # acceptable, as long as nothing was silently clobbered.
            assert new_res.get("retryable") or "concurrent" in (new_res.get("error") or ""), (
                f"new fix failed non-retryably against a benign revert: {new_res!r}"
            )
        else:
            # revert failed — must be the explicit, honest
            # file_changed_since_fix (or an equivalent retryable
            # error), never a silent success that actually clobbered
            # something.
            assert revert_res.get("ok") is False
            assert "NEW_FIX_APPLIED" in final_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
