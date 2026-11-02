"""tests/test_regression_iter2026_49_part2c_c1_sibling_revert_ledger.py

Part 2c C1 [High] — services/finding_fix_applier.py::apply_file_fixes
writes ONE `finding_fixes` row PER FINDING, but all siblings resolved
by the same commit share the SAME `commit_sha`/`parent_commit_sha`
(one LLM call, one commit, per Part 2b R2). `_revert_single_fix_on_batch`
restores the WHOLE FILE to the parent (pre-commit) content — which
undoes EVERY finding that commit fixed — but the caller
(`revert_finding_fix`) only marks the ONE fix row `reverted` and only
reopens that ONE finding.

Result: after reverting finding A (sibling of B in the same commit),
the code is vulnerable to BOTH A and B again, but `cto_open_findings`/
`fixed_findings` still say B is "fixed" — a rescan hides it. A second
revert attempt on B then fails with `file_changed_since_fix` (the
branch content, now reverted, no longer matches the fix's own
recorded "after" content — which is identical to A's, since they
share `commit_sha`).

Fix: a revert of any row that shares a `commit_sha` with sibling rows
must revert the commit's file ONCE and, in the SAME operation, mark
ALL sibling rows reverted, reopen ALL their findings, and remove ALL
their `fixed_findings` ledger rows. A second revert of any sibling is
an idempotent no-op success.
"""
from __future__ import annotations

import pytest

import services.finding_fix_applier as ffa

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
                for k in (update.get("$unset") or {}):
                    d.pop(k, None)
                return

    async def update_many(self, filt, update):
        n = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                d.update(update.get("$set", {}))
                for k in (update.get("$unset") or {}):
                    d.pop(k, None)
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


class _FakeDB:
    def __init__(self):
        self.cto_projects = _FakeColl([{
            "project_id": "p1", "user_id": "u1",
            "github_owner": "o", "github_repo": "r",
        }])
        self.finding_fixes = _FakeColl()
        self.fix_batches = _FakeColl()
        self.cto_open_findings = _FakeColl()
        self.fixed_findings = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


def _two_sibling_rows(*, commit_sha="after_shared" * 4, parent_sha="parent_shared"):
    """Two findings (rule_a @ line 1, rule_b @ line 2) in the SAME
    file, resolved by the SAME apply_file_fixes commit — the exact
    shape apply_file_fixes writes today (one row per finding, shared
    commit_sha/parent_commit_sha)."""
    return [
        {"fix_id": "fx_a", "user_id": "u1", "project_id": "p1",
         "batch_id": "fb_1", "file": "a.py", "rule_id": "rule_a", "line": 1,
         "branch": "aurem/fixes-aaaaaaaa",
         "parent_commit_sha": parent_sha, "commit_sha": commit_sha},
        {"fix_id": "fx_b", "user_id": "u1", "project_id": "p1",
         "batch_id": "fb_1", "file": "a.py", "rule_id": "rule_b", "line": 2,
         "branch": "aurem/fixes-aaaaaaaa",
         "parent_commit_sha": parent_sha, "commit_sha": commit_sha},
    ]


def _seed_findings_and_ledger(db):
    from services.fixed_findings import finding_key
    db.cto_open_findings.docs.extend([
        {"user_id": "u1", "project_id": "p1", "file": "a.py",
         "rule_id": "rule_a", "line": 1, "status": "fixed", "finding_id": "fa"},
        {"user_id": "u1", "project_id": "p1", "file": "a.py",
         "rule_id": "rule_b", "line": 2, "status": "fixed", "finding_id": "fb"},
    ])
    for rid, line in (("rule_a", 1), ("rule_b", 2)):
        key = finding_key({"rule_id": rid, "file": "a.py", "line": line})
        db.fixed_findings.docs.append({"user_id": "u1", "project_id": "p1", "key": key})


def _mock_git(monkeypatch, *, parent_sha, after_sha, branch):
    _by_ref = {
        parent_sha: "BEFORE both findings\n",
        after_sha: "AFTER both findings fixed\n",
        branch: "AFTER both findings fixed\n",  # current branch content
    }

    async def _fetch(owner, repo, ref, path, token):
        return _by_ref[ref], None
    monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

    commits = []

    async def _commit(**kw):
        commits.append(kw)
        new_content = kw["files"][kw.get("files") and next(iter(kw["files"]))]
        _by_ref[branch] = new_content
        return {"sha": "r1", "full_sha": "r1" * 20, "html_url": ""}
    monkeypatch.setattr("services.github_api_writer.commit_files", _commit)
    return commits, _by_ref


class TestC1SiblingRevertCorruptsLedger:
    async def test_reverting_one_sibling_leaves_the_other_falsely_marked_fixed(self, monkeypatch):
        """CONFIRM — the bug as shipped: revert fx_a; fx_b must NOT
        still read as fixed once the file content no longer contains
        either fix."""
        db = _FakeDB()
        rows = _two_sibling_rows()
        db.finding_fixes.docs.extend(rows)
        _seed_findings_and_ledger(db)
        parent_sha, after_sha, branch = (
            rows[0]["parent_commit_sha"], rows[0]["commit_sha"], rows[0]["branch"],
        )
        _, by_ref = _mock_git(monkeypatch, parent_sha=parent_sha,
                              after_sha=after_sha, branch=branch)

        result = await ffa.revert_finding_fix(db, fix_id="fx_a", token="tok",
                                               user_id="u1", project_id="p1")
        assert result["ok"] is True

        # The file-level truth: the commit that fixed BOTH findings
        # has been undone — the branch content is back to "before".
        assert by_ref[branch] == "BEFORE both findings\n"

        fb_row = await db.finding_fixes.find_one({"fix_id": "fx_b"})
        fb_finding = await db.cto_open_findings.find_one(
            {"user_id": "u1", "project_id": "p1", "rule_id": "rule_b"},
        )
        # THE BUG (pre-fix): fx_b is not marked reverted and its
        # finding still reads "fixed" even though the code protecting
        # it was just undone by this same revert.
        assert fb_row.get("reverted") is True, (
            "C1 FIX REQUIRED: sibling fx_b must be marked reverted "
            "when fx_a (same commit_sha) is reverted — the whole "
            "file's protection was undone in one operation."
        )
        assert fb_finding["status"] == "open", (
            "C1 FIX REQUIRED: rule_b's finding must reopen — the code "
            "is vulnerable again after fx_a's revert undid the shared commit."
        )
        from services.fixed_findings import finding_key
        key_b = finding_key({"rule_id": "rule_b", "file": "a.py", "line": 2})
        ledger_row_b = await db.fixed_findings.find_one(
            {"user_id": "u1", "project_id": "p1", "key": key_b},
        )
        assert ledger_row_b is None, (
            "C1 FIX REQUIRED: fixed_findings ledger row for rule_b must "
            "be dropped too, or the next rescan keeps hiding it."
        )

    async def test_second_revert_of_sibling_is_an_idempotent_no_op_not_a_file_changed_error(self, monkeypatch):
        """CONFIRM — pre-fix, a second revert call on fx_b (after
        fx_a's revert already happened) fails with
        `file_changed_since_fix` because the branch no longer matches
        fx_b's own recorded 'after' content (identical to fx_a's,
        since they share commit_sha). Post-fix this must be a clean
        idempotent no-op."""
        db = _FakeDB()
        rows = _two_sibling_rows()
        db.finding_fixes.docs.extend(rows)
        _seed_findings_and_ledger(db)
        parent_sha, after_sha, branch = (
            rows[0]["parent_commit_sha"], rows[0]["commit_sha"], rows[0]["branch"],
        )
        _mock_git(monkeypatch, parent_sha=parent_sha, after_sha=after_sha, branch=branch)

        r1 = await ffa.revert_finding_fix(db, fix_id="fx_a", token="tok",
                                           user_id="u1", project_id="p1")
        assert r1["ok"] is True

        r2 = await ffa.revert_finding_fix(db, fix_id="fx_b", token="tok",
                                           user_id="u1", project_id="p1")
        assert r2["ok"] is True, (
            "C1 FIX REQUIRED: reverting a sibling of an already-"
            f"reverted commit must be a clean no-op, got: {r2}"
        )
        assert r2.get("already_reverted") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
