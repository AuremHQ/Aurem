"""tests/test_regression_iter2026_34_single_fix_branch_apply_fix.py

FIX_one_branch_for_all_fixes — services/finding_fix_applier.py:
  H1: a 2nd fix to the SAME file on the batch branch reads the 1st
      fix's already-landed content, not the stale base-branch copy.
  H2: branch-creation failure is a hard error — NOTHING is committed
      to the base branch (the old direct-to-base fallback is gone).
  H3: reverting ONE batch fix never closes the PR or touches another
      fix; revert_batch() is the explicit, separate whole-PR close.
  skip-no-change: an idempotent re-run (patch == current content) is
      ok=True, no commit, no charge — tested at the router layer in
      the billing-focused assertions here (service-level ok flag).

GitHub is mocked at the services.loop_safety / services.
github_api_writer helper boundary; Mongo via the repo's dict-fake-DB
pattern (same convention as tests/test_iter212m114_real_finding_fix.py).
"""
from __future__ import annotations

import pytest

import services.finding_fix_applier as ffa
import services.fix_batches as fb
import services.loop_safety as ls

pytestmark = pytest.mark.asyncio


# ───────────────────────────── fake DB double ────────────────────────────
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
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                d.update(update.get("$set", {}))
                return
        return

    async def update_many(self, filt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                d.update(update.get("$set", {}))

    def find(self, filt, proj=None):
        matched = [dict(d) for d in self.docs if all(d.get(k) == v for k, v in (filt or {}).items())]
        class _Cur:
            def __init__(self, items):
                self.items = items
            async def to_list(self, length=None):
                return list(self.items)[: (length if length is not None else len(self.items))]
        return _Cur(matched)

    async def delete_many(self, filt):
        self.docs = [d for d in self.docs if not all(d.get(k) == v for k, v in (filt or {}).items())]


class _FakeFixBatches(_FakeColl):
    async def find_one_and_update(self, filt, update, return_document=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                for k, v in update.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                for k, v in update.get("$push", {}).items():
                    d.setdefault(k, []).append(v)
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


@pytest.fixture(autouse=True)
def _reset_locks():
    fb._batch_locks.clear()
    yield
    fb._batch_locks.clear()


def _mock_pat(monkeypatch):
    async def _get_token(proj):
        return "ghp_tok", None, None
    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error", _get_token)


def _mock_batch_github(monkeypatch, *, pr_number=55):
    async def _create_branch(**kw):
        return True, None

    async def _open_pr(**kw):
        return {"ok": True, "pr_url": f"https://github.com/o/r/pull/{pr_number}",
                "pr_number": pr_number, "reused": False, "error": None}

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


def _finding(rule_id="rule_a", file="a.py", line=1):
    return {"rule_id": rule_id, "file": file, "line": line,
            "severity": "high", "title": "t", "message": "m"}


class TestH1SameFileSequentialReads:
    async def test_second_fix_in_same_file_sees_first_fixs_patch(self, monkeypatch):
        """The classic H1 repro: fix #1 patches a.py on the batch
        branch; fix #2 (a DIFFERENT finding, same file) must read
        fix #1's already-landed content, not the stale base copy."""
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        branch_content = {"main": "line1\nBAD_A\nBAD_B\n"}

        async def _fetch(owner, repo, branch, path, token):
            if branch in branch_content:
                return branch_content[branch], None
            return "", "file_not_found"

        async def _llm(*, path, current_content, finding, user_id, db, review_mode=None):
            fixed = current_content.replace(
                "BAD_A" if finding["rule_id"] == "rule_a" else "BAD_B",
                "GOOD",
            )
            return fixed, None

        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)
        monkeypatch.setattr(ffa, "_generate_patched_content", _llm)

        # Part 2b R1 — _finding_still_present is now ALSO called as a
        # pre-check on the UNPATCHED content (before the LLM runs), so
        # a blanket `False` would wrongly short-circuit fix #1 as
        # "already not present". Content-aware: the marker for THIS
        # rule is present until the LLM actually replaces it.
        def _still_present(patched_content, path, finding):
            marker = "BAD_A" if finding["rule_id"] == "rule_a" else "BAD_B"
            return marker in patched_content
        monkeypatch.setattr(ffa, "_finding_still_present", _still_present)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            new_content = kw["files"]["a.py"]
            branch_content[kw["branch"]] = new_content
            return {"sha": f"sha{len(commits)}", "full_sha": f"sha{len(commits)}" * 8,
                    "html_url": "https://github.com/o/r/commit/x"}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        res1 = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u1"}, project_id="p1",
            finding=_finding(rule_id="rule_a", file="a.py"),
        )
        assert res1["ok"] is True
        batch_branch = res1["branch"]
        assert "GOOD" in branch_content[batch_branch]
        assert "BAD_B" in branch_content[batch_branch]  # fix #1 only touched BAD_A

        res2 = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u1"}, project_id="p1",
            finding=_finding(rule_id="rule_b", file="a.py"),
        )
        assert res2["ok"] is True
        assert res2["branch"] == batch_branch  # SAME batch, H1's precondition

        # The critical H1 assertion: BOTH fixes survive in the final
        # branch content — fix #2 did not read/overwrite from base.
        final = branch_content[batch_branch]
        assert "GOOD" in final
        assert "BAD_A" not in final
        assert "BAD_B" not in final
        assert len(commits) == 2  # one commit per fix, not a squashed overwrite


class TestH2NoDirectToBaseFallback:
    async def test_branch_create_failure_commits_nothing_to_base(self, monkeypatch):
        _mock_pat(monkeypatch)

        async def _create_branch(**kw):
            return False, "base_ref_status_500"
        monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "x", "full_sha": "x" * 40, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        db = _FakeDB()
        res = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u1"}, project_id="p1", finding=_finding(),
        )
        assert res["ok"] is False
        assert commits == []  # H2 — literally nothing committed anywhere
        assert db.finding_fixes.docs == []


class TestSkipNoChange:
    async def test_idempotent_rerun_skips_commit_and_is_marked_not_charged(self, monkeypatch):
        """Part 2b R1 — a scanner-checkable finding now gets caught by
        the presence pre-check (test file 40's `skipped_not_present`
        covers that). The ONLY remaining legitimate `skipped_no_change`
        case is a finding with NO rule_id to check against the scanner
        at all (e.g. a chat-audit style finding) — that's what this
        test now exercises."""
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return "already fixed content", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        async def _llm_no_change(*, path, current_content, finding, user_id, db, review_mode=None):
            return "", "llm_no_change"
        monkeypatch.setattr(ffa, "_generate_patched_content", _llm_no_change)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "x", "full_sha": "x" * 40, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        no_rule_finding = {"file": "a.py", "line": 1, "severity": "high",
                            "title": "t", "message": "m"}  # no rule_id/rule key
        res = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u1"}, project_id="p1", finding=no_rule_finding,
        )
        assert res["ok"] is True
        assert res["skipped_no_change"] is True
        assert commits == []


class TestH3RevertIsolation:
    async def test_single_fix_revert_restores_file_without_closing_pr(self, monkeypatch):
        db = _FakeDB()
        db.cto_projects.docs.append({
            "project_id": "p1", "github_owner": "o", "github_repo": "r",
        })
        db.finding_fixes.docs.append({
            "fix_id": "fx_1", "user_id": "u1", "project_id": "p1",
            "batch_id": "fb_1", "file": "a.py", "rule_id": "rule_a",
            "branch": "aurem/fixes-aaaaaaaa",
            "parent_commit_sha": "parent_a", "commit_sha": "after_a",
        })
        db.finding_fixes.docs.append({
            "fix_id": "fx_2", "user_id": "u1", "project_id": "p1",
            "batch_id": "fb_1", "file": "b.py", "rule_id": "rule_b",
            "branch": "aurem/fixes-aaaaaaaa",
            "parent_commit_sha": "parent_b", "commit_sha": "after_b",
        })

        # Part 2b R3 — revert re-fetches BOTH the pre-fix content (at
        # parent_commit_sha) and the post-fix content (at commit_sha)
        # from GitHub, plus the branch's CURRENT content, instead of
        # reading a Mongo-stored snapshot.
        _by_ref = {
            "parent_a": "BEFORE", "after_a": "AFTER",
            "parent_b": "B_BEFORE", "after_b": "B_AFTER",
            "aurem/fixes-aaaaaaaa": None,  # set per-test below
        }
        _branch_content = {"a.py": "AFTER", "b.py": "B_AFTER"}
        async def _fetch(owner, repo, ref, path, token):
            if ref == "aurem/fixes-aaaaaaaa":
                return _branch_content[path], None
            return _by_ref[ref], None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        close_calls = []

        async def _close_pr(**kw):
            close_calls.append(kw)
            return True, None
        monkeypatch.setattr(ls, "close_pr", _close_pr)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "r1", "full_sha": "r1" * 20, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        result = await ffa.revert_finding_fix(db, fix_id="fx_1", token="tok")
        assert result["ok"] is True
        assert close_calls == []  # H3 — the PR was NEVER touched
        assert len(commits) == 1
        assert commits[0]["files"] == {"a.py": "BEFORE"}
        assert db.finding_fixes.docs[0]["reverted"] is True
        assert db.finding_fixes.docs[1].get("reverted") is not True  # fix #2 untouched

    async def test_refuses_to_revert_if_file_changed_since(self, monkeypatch):
        db = _FakeDB()
        db.cto_projects.docs.append({
            "project_id": "p1", "github_owner": "o", "github_repo": "r",
        })
        db.finding_fixes.docs.append({
            "fix_id": "fx_1", "user_id": "u1", "project_id": "p1",
            "batch_id": "fb_1", "file": "a.py", "rule_id": "rule_a",
            "branch": "aurem/fixes-aaaaaaaa",
            "parent_commit_sha": "parent_a", "commit_sha": "after_a",
        })

        async def _fetch(owner, repo, ref, path, token):
            if ref == "parent_a":
                return "BEFORE", None
            if ref == "after_a":
                return "AFTER", None
            return "SOMEONE_ELSE_CHANGED_THIS", None  # current branch content diverged
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "x", "full_sha": "x" * 40, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        result = await ffa.revert_finding_fix(db, fix_id="fx_1", token="tok")
        assert result["ok"] is False
        assert result["error"] == "file_changed_since_fix"
        assert commits == []  # refused, never clobbered

    async def test_already_reverted_is_idempotent(self, monkeypatch):
        db = _FakeDB()
        db.finding_fixes.docs.append({
            "fix_id": "fx_1", "user_id": "u1", "project_id": "p1",
            "batch_id": "fb_1", "reverted": True,
        })
        result = await ffa.revert_finding_fix(db, fix_id="fx_1", token="tok")
        assert result["ok"] is True
        assert result["already_reverted"] is True

    async def test_revert_batch_closes_pr_and_marks_every_fix_reverted(self, monkeypatch):
        db = _FakeDB()
        db.cto_projects.docs.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "o", "github_repo": "r",
        })
        db.fix_batches.docs.append({
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "pr_number": 5,
            "status": "open",
        })
        db.finding_fixes.docs.extend([
            {"fix_id": "fx_1", "batch_id": "fb_1"},
            {"fix_id": "fx_2", "batch_id": "fb_1"},
        ])

        async def _close_pr(**kw):
            return True, None
        monkeypatch.setattr(ls, "close_pr", _close_pr)

        async def _delete_branch(**kw):
            return False, "GW_BLOCK_non_namespaced_branch"  # aurem/fixes-* isn't auremcto/
        monkeypatch.setattr(ls, "delete_ship_branch", _delete_branch)

        result = await ffa.revert_batch(
            db, batch_id="fb_1", user_id="u1", project_id="p1", token="tok",
        )
        assert result["ok"] is True
        assert result["pr_closed"] is True
        batch = await db.fix_batches.find_one({"batch_id": "fb_1"})
        assert batch["status"] == "closed"
        for doc in db.finding_fixes.docs:
            assert doc["reverted"] is True

    async def test_revert_batch_requires_ownership(self):
        db = _FakeDB()
        db.fix_batches.docs.append({
            "batch_id": "fb_1", "user_id": "userB", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "pr_number": 5, "status": "open",
        })
        result = await ffa.revert_batch(
            db, batch_id="fb_1", user_id="userA", project_id="p1", token="tok",
        )
        assert result["ok"] is False
        assert result["error"] == "batch_not_found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
