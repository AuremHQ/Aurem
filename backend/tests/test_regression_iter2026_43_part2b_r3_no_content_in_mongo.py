"""tests/test_regression_iter2026_43_part2b_r3_no_content_in_mongo.py

Part 2b R3 — `finding_fixes` rows used to store the FULL plaintext
before/after file content for every fix. For a hardcoded-secret
finding, that meant the secret value itself (both the original leak
AND, often, a leftover copy in the "after" snapshot) sat in Mongo in
plaintext forever, outside the normal Git access-control/audit trail.

Fix: git already holds both versions (`parent_commit_sha` captured
right before the commit, `commit_sha` from the commit itself) — revert
re-fetches from GitHub by sha instead of reading a second plaintext
copy out of Mongo. No fix ever persists `original_content` /
`patched_content` to the `finding_fixes` collection.
"""
from __future__ import annotations

import pytest

import services.finding_fix_applier as ffa
import services.fix_batches as fb
import services.loop_safety as ls

pytestmark = pytest.mark.asyncio

_SECRET = "sk_live_THIS_IS_A_FAKE_SECRET_VALUE_1234567890"


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


_FORBIDDEN_KEYS = ("original_content", "patched_content", "content", "before", "after")


class TestNoPlaintextContentInFindingFixes:
    async def test_apply_finding_fix_never_persists_content_only_shas(self, monkeypatch):
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return f"const KEY = '{_SECRET}'", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        async def _llm(*, path, current_content, finding, user_id, db, review_mode=None):
            return "const KEY = os.environ.get('KEY')  # TODO set KEY", None
        monkeypatch.setattr(ffa, "_generate_patched_content", _llm)
        monkeypatch.setattr(ffa, "_finding_still_present",
                             lambda patched_content, path, finding: _SECRET in patched_content)

        async def _get_branch_head(owner, repo, branch, token):
            return {"sha": "parent_sha_abc"}
        monkeypatch.setattr("services.github_api_writer._get_branch_head", _get_branch_head)

        async def _commit(**kw):
            return {"sha": "s1", "full_sha": "s1" * 20, "html_url": "https://x"}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        res = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u1"}, project_id="p1",
            finding={"id": "f1", "rule_id": "hardcoded_secret", "file": "a.py", "line": 1},
        )
        assert res["ok"] is True
        assert len(db.finding_fixes.docs) == 1
        row = db.finding_fixes.docs[0]
        for key in _FORBIDDEN_KEYS:
            assert key not in row, f"{key!r} must never be persisted to finding_fixes"
        # The secret value itself must not appear ANYWHERE in the
        # persisted document, under any key.
        row_text = repr(row)
        assert _SECRET not in row_text
        # The git-sha-based revert contract IS present.
        assert row["parent_commit_sha"] == "parent_sha_abc"
        assert row["commit_sha"] == "s1" * 20

    async def test_apply_file_fixes_never_persists_content_only_shas(self, monkeypatch):
        _mock_pat(monkeypatch)
        _mock_batch_github(monkeypatch)
        db = _FakeDB()

        async def _fetch(owner, repo, branch, path, token):
            return f"const KEY = '{_SECRET}'\nconst OTHER = 'y'", None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        async def _llm_multi(*, path, current_content, findings, user_id, db, review_mode=None):
            return "const KEY = os.environ.get('KEY')\nconst OTHER = 'y'", None
        monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
        monkeypatch.setattr(ffa, "_finding_still_present",
                             lambda patched_content, path, finding: _SECRET in patched_content)

        async def _get_branch_head(owner, repo, branch, token):
            return {"sha": "parent_sha_xyz"}
        monkeypatch.setattr("services.github_api_writer._get_branch_head", _get_branch_head)

        async def _commit(**kw):
            return {"sha": "s2", "full_sha": "s2" * 20, "html_url": "https://x"}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        res = await ffa.apply_file_fixes(
            db=db, user={"user_id": "u1"}, project_id="p1", file="a.py",
            findings=[{"id": "f1", "rule_id": "hardcoded_secret", "file": "a.py", "line": 1}],
        )
        assert res["ok"] is True
        assert len(db.finding_fixes.docs) == 1
        row = db.finding_fixes.docs[0]
        for key in _FORBIDDEN_KEYS:
            assert key not in row
        assert _SECRET not in repr(row)
        assert row["parent_commit_sha"] == "parent_sha_xyz"

    async def test_revert_re_fetches_from_github_never_reads_a_mongo_snapshot(self, monkeypatch):
        """A finding_fixes row has NO content fields to read in the
        first place — revert's only path to the before/after bytes is
        re-fetching from GitHub by sha, proven here by a fake DB whose
        row carries no content keys at all, yet revert still
        succeeds using only parent_commit_sha/commit_sha."""
        db = _FakeDB()
        db.cto_projects.docs.append({
            "project_id": "p1", "github_owner": "o", "github_repo": "r",
        })
        db.finding_fixes.docs.append({
            "fix_id": "fx_1", "user_id": "u1", "project_id": "p1",
            "batch_id": "fb_1", "file": "a.py", "rule_id": "hardcoded_secret",
            "branch": "aurem/fixes-aaaaaaaa",
            "parent_commit_sha": "parent_sha", "commit_sha": "after_sha",
            # Deliberately no content/original_content/patched_content keys.
        })

        _by_ref = {
            "parent_sha": f"const KEY = '{_SECRET}'",
            "after_sha": "const KEY = os.environ.get('KEY')",
            "aurem/fixes-aaaaaaaa": "const KEY = os.environ.get('KEY')",
        }

        async def _fetch(owner, repo, ref, path, token):
            return _by_ref[ref], None
        monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

        commits = []

        async def _commit(**kw):
            commits.append(kw)
            return {"sha": "r1", "full_sha": "r1" * 20, "html_url": ""}
        monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

        result = await ffa.revert_finding_fix(db, fix_id="fx_1", token="tok")
        assert result["ok"] is True
        assert len(commits) == 1
        assert _SECRET in commits[0]["files"]["a.py"]  # restored FROM GitHub, not Mongo


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
