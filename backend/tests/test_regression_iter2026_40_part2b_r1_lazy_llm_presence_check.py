"""tests/test_regression_iter2026_40_part2b_r1_lazy_llm_presence_check.py

Part 2b R1 — services/finding_fix_applier.py used to treat an LLM
echoing the input back unchanged (`llm_no_change`) as an unconditional
success ("already fixed, nothing to commit"), even when the finding
was CONFIRMED still present in the content just fetched. A lazy/
refusing LLM call therefore masqueraded as a successful fix with zero
proof anything changed.

Fix: a presence pre-check (`_finding_still_present` on the UNPATCHED
content) now runs BEFORE any LLM call, for every scanner-checkable
finding (has a rule_id). Three outcomes:
  * already absent before the LLM runs  -> skipped_not_present=True,
    LLM is NEVER called.
  * present, LLM echoes unchanged       -> ok=False,
    error="llm_made_no_change", retryable=True (a real failure).
  * no rule_id to check (chat-audit)    -> old skipped_no_change
    behavior preserved (can't independently verify presence).

Covers both apply_finding_fix (single-finding) and apply_file_fixes
(per-file, Part 2b R2) — the presence pre-check is applied identically
in both entry points.
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


def _finding(rule_id="rule_a", file="a.py", line=1):
    return {"id": "f1", "rule_id": rule_id, "file": file, "line": line,
            "severity": "high", "title": "t", "message": "m"}


class TestR1PresenceCheckBeforeLlm:
    async def test_already_absent_is_skipped_without_ever_calling_the_llm(self, monkeypatch):
        """The finding is ALREADY gone from the fetched content (e.g.
        a retried bulk job hitting a batch branch that already
        carries this exact fix). The presence pre-check must catch
        this BEFORE the LLM is invoked — proven by making the LLM
        mock raise if it's ever called at all."""
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return "clean content, no issue here", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        llm_calls = []

        async def _llm(*, path, current_content, finding, user_id, db, review_mode=None):
            llm_calls.append(1)
            raise AssertionError("LLM must never be called when finding is already absent")
        monkeypatch.setattr(ffa, "_generate_patched_content", _llm)

        def _not_present(patched_content, path, finding):
            return False  # the rule never fires on this content
        monkeypatch.setattr(ffa, "_finding_still_present", _not_present)

        res = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u1"}, project_id="p1", finding=_finding(),
        )
        assert res["ok"] is True
        assert res["skipped_not_present"] is True
        assert llm_calls == []
        assert db.finding_fixes.docs == []  # nothing committed, nothing to persist

    async def test_llm_echoes_unchanged_while_finding_confirmed_present_is_a_real_failure(self, monkeypatch):
        """R1's core fix: a scanner-checkable finding that IS present
        on the fetched content, but whose LLM call echoes the input
        back verbatim (llm_no_change), must be a real, retryable
        FAILURE — never masquerade as 'already fixed'."""
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return "BAD_STUFF still here", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        async def _llm_no_change(*, path, current_content, finding, user_id, db, review_mode=None):
            return "", "llm_no_change"
        monkeypatch.setattr(ffa, "_generate_patched_content", _llm_no_change)

        def _present(patched_content, path, finding):
            return "BAD_STUFF" in patched_content
        monkeypatch.setattr(ffa, "_finding_still_present", _present)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "x", "full_sha": "x" * 40, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        res = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u1"}, project_id="p1", finding=_finding(),
        )
        assert res["ok"] is False
        assert res["error"] == "llm_made_no_change"
        assert res["retryable"] is True
        assert commits == []
        assert db.finding_fixes.docs == []

    async def test_apply_file_fixes_applies_the_same_precheck_per_finding(self, monkeypatch):
        """apply_file_fixes (R2's per-file entry point) must apply the
        IDENTICAL presence pre-check per finding before the one
        multi-finding LLM call — a finding already resolved by an
        earlier fix in the same batch is skipped, never resent to the
        LLM, while a genuinely-present sibling finding in the SAME
        file still gets fixed."""
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        content = "BAD_B only — rule_a was already cleaned up earlier"

        async def _fetch(owner, repo, branch, path, token):
            return content, None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        multi_calls = []

        async def _llm_multi(*, path, current_content, findings, user_id, db, review_mode=None):
            multi_calls.append([f["rule_id"] for f in findings])
            return current_content.replace("BAD_B", "GOOD_B"), None
        monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)

        def _present(patched_content, path, finding):
            marker = "BAD_A" if finding["rule_id"] == "rule_a" else "BAD_B"
            return marker in patched_content
        monkeypatch.setattr(ffa, "_finding_still_present", _present)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "x", "full_sha": "x" * 40, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        findings = [
            {"id": "fa", "rule_id": "rule_a", "file": "a.py", "line": 1},
            {"id": "fb", "rule_id": "rule_b", "file": "a.py", "line": 2},
        ]
        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1",
            file="a.py", findings=findings,
        )
        assert res["ok"] is True
        # Only rule_b was sent to the LLM — rule_a's pre-check already
        # found it absent.
        assert multi_calls == [["rule_b"]]
        by_id = {r["finding_id"]: r for r in res["results"]}
        assert by_id["fa"]["status"] == "skipped_not_present"
        assert by_id["fb"]["status"] == "resolved"
        assert len(commits) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
