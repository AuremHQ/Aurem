"""tests/test_regression_iter2026_41_part2b_r2_apply_file_fixes.py

Part 2b R2 — "Fix this file" used to route through the chat composer
instead of a real per-file fix, and no server-side `apply_file_fixes`
(or bulk-by-file) ever existed. This is the real thing: ONE LLM call
covering every finding in a file, ONE commit, with its own regression
gate and optimistic-concurrency retry — services/finding_fix_applier.py
::apply_file_fixes.
"""
from __future__ import annotations

import pytest

import services.finding_fix_applier as ffa
import services.fix_batches as fb
import services.loop_safety as ls

pytestmark = pytest.mark.asyncio


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
                    if isinstance(v, dict) and "$each" in v:
                        d.setdefault("rows", []).extend(v["$each"])
                    else:
                        d.setdefault("rows", []).append(v)
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                return dict(d)
        return None


class _FakeDB:
    def __init__(self, status="open"):
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


def _mock_batch_github(monkeypatch):
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


def _findings():
    return [
        {"id": "fa", "rule_id": "rule_a", "file": "a.py", "line": 1, "severity": "high"},
        {"id": "fb", "rule_id": "rule_b", "file": "a.py", "line": 2, "severity": "medium"},
    ]


def _present_by_marker(patched_content, path, finding):
    marker = f"FIXED[{finding['rule_id']}]"
    return marker not in patched_content  # "still present" == marker not yet added


class TestApplyFileFixesCore:
    async def test_one_llm_call_one_commit_resolves_both_findings(self, monkeypatch):
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return "orig content with BAD_A and BAD_B", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        multi_calls = []

        async def _llm_multi(*, path, current_content, findings, user_id, db):
            multi_calls.append(1)
            out = current_content
            for f in findings:
                out += f"\nFIXED[{f['rule_id']}]"
            return out, None
        monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
        monkeypatch.setattr(ffa, "_finding_still_present", _present_by_marker)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "s1", "full_sha": "s1" * 20, "html_url": "https://x"}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1",
            file="a.py", findings=_findings(),
        )
        assert res["ok"] is True
        assert len(multi_calls) == 1
        assert len(commits) == 1
        statuses = {r["finding_id"]: r["status"] for r in res["results"]}
        assert statuses == {"fa": "resolved", "fb": "resolved"}
        # Part 2b R6 — resolved entries carry a fix_id for the undo UI.
        for r in res["results"]:
            assert r.get("fix_id")
        assert len(db.finding_fixes.docs) == 2

    async def test_partial_resolution_commits_only_resolved_marks_rest_remaining(self, monkeypatch):
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return "orig with BAD_A and BAD_B", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        async def _llm_multi(*, path, current_content, findings, user_id, db):
            # Only fixes rule_a — rule_b's marker never gets added.
            return current_content + "\nFIXED[rule_a]", None
        monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
        monkeypatch.setattr(ffa, "_finding_still_present", _present_by_marker)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "s1", "full_sha": "s1" * 20, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1",
            file="a.py", findings=_findings(),
        )
        assert res["ok"] is True
        assert len(commits) == 1  # the resolved one still committed
        statuses = {r["finding_id"]: r["status"] for r in res["results"]}
        assert statuses == {"fa": "resolved", "fb": "remaining"}
        assert len(db.finding_fixes.docs) == 1

    async def test_whole_file_llm_failure_marks_every_active_finding_error_nothing_committed(self, monkeypatch):
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return "orig with BAD_A and BAD_B", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        async def _llm_multi(*, path, current_content, findings, user_id, db):
            return "", "llm_empty_response"
        monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
        monkeypatch.setattr(ffa, "_finding_still_present", _present_by_marker)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "s1", "full_sha": "s1" * 20, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1",
            file="a.py", findings=_findings(),
        )
        assert commits == []
        statuses = {r["finding_id"]: (r["status"], r.get("error")) for r in res["results"]}
        assert statuses["fa"] == ("error", "llm_empty_response")
        assert statuses["fb"] == ("error", "llm_empty_response")
        assert db.finding_fixes.docs == []

    async def test_regression_gate_rejects_patch_that_introduces_a_new_scanner_category(self, monkeypatch):
        """A patch must never trade one issue for a new one — if the
        scanner finds a category on the patched content that wasn't
        present before, the WHOLE commit is rejected."""
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return "orig with BAD_A and BAD_B", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        async def _llm_multi(*, path, current_content, findings, user_id, db):
            out = current_content
            for f in findings:
                out += f"\nFIXED[{f['rule_id']}]"
            return out, None
        monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
        monkeypatch.setattr(ffa, "_finding_still_present", _present_by_marker)

        import services.vanguard_scanner as vs

        def _scan(content, path):
            if "FIXED[" in content:
                return [{"name": "new_bad_category"}]
            return []
        monkeypatch.setattr(vs, "scan_text", _scan)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "s1", "full_sha": "s1" * 20, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1",
            file="a.py", findings=_findings(),
        )
        assert res["ok"] is False
        assert res["error"] == "patch_introduced_new_issue"
        assert commits == []
        for r in res["results"]:
            assert r["status"] == "error"
            assert r["error"] == "patch_introduced_new_issue"

    async def test_optimistic_concurrency_regenerates_once_when_file_drifted(self, monkeypatch):
        """The batch branch content changes between the outside-the-
        lock fetch and the commit-phase re-check (another fix landed
        on this same file meanwhile) — must regenerate the patch ONCE
        against the fresh content rather than clobber it."""
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        branch_content = {"main": "orig with BAD_A and BAD_B"}
        fetch_calls = []

        async def _fetch(owner, repo, branch, path, token):
            fetch_calls.append(branch)
            # First read sees the stale copy; a "concurrent" write
            # lands on the real branch key BEFORE the 2nd (in-lock) read.
            if len(fetch_calls) == 1:
                return branch_content["main"], None
            branch_content[branch] = "DRIFTED content with BAD_A and BAD_B — someone else wrote first"
            return branch_content[branch], None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        multi_calls = []

        async def _llm_multi(*, path, current_content, findings, user_id, db):
            multi_calls.append(current_content)
            out = current_content
            for f in findings:
                out += f"\nFIXED[{f['rule_id']}]"
            return out, None
        monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
        monkeypatch.setattr(ffa, "_finding_still_present", _present_by_marker)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "s1", "full_sha": "s1" * 20, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        # Need a 3rd, non-drifting read for the "did it drift AGAIN"
        # re-check right after regenerating.
        real_fetch = ffa._fetch_file_content

        async def _fetch3(owner, repo, branch, path, token):
            if len(fetch_calls) >= 2:
                return branch_content.get(branch, branch_content["main"]), None
            return await real_fetch(owner, repo, branch, path, token)
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch3)

        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1",
            file="a.py", findings=_findings(),
        )
        assert res["ok"] is True
        assert len(multi_calls) == 2  # original + one regenerate
        assert "DRIFTED" in multi_calls[1]
        assert len(commits) == 1

    async def test_missing_required_args_fails_cleanly(self):
        db = _FakeDB()
        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1",
            file="", findings=[],
        )
        assert res["ok"] is False
        assert res["error"] == "missing_required_args"
        assert res["results"] == []

    async def test_batch_unavailable_propagates_as_clean_failure(self, monkeypatch):
        _mock_pat(monkeypatch)
        db = _FakeDB()

        async def _create_branch(**kw):
            return False, "base_ref_status_500"
        monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)

        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1",
            file="a.py", findings=_findings(),
        )
        assert res["ok"] is False
        assert res["results"] == []
        assert db.finding_fixes.docs == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
