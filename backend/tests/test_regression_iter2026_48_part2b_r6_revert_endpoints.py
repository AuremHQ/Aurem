"""tests/test_regression_iter2026_48_part2b_r6_revert_endpoints.py

Part 2b R6 — there were NO revert HTTP routes at all (the service-
level revert_finding_fix/revert_batch existed but nothing in
routers/fix_pipeline.py ever called them), and the service function
itself had no ownership check — any caller who learned a fix_id could
revert ANOTHER user's fix.

Fix: POST /fix-pipeline/fixes/{fix_id}/revert and
POST /fix-pipeline/batches/{batch_id}/revert, both rate-limited, both
resolving the GitHub token server-side from the fix's/batch's OWN
project (never from the request body), both denying a foreign
fix_id/batch_id as "not_found" rather than leaking existence. A
successful revert reopens the finding in cto_open_findings and drops
its fixed_findings ledger row so the next rescan doesn't keep hiding
it.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

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
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                d.update(update.get("$set", {}))
                for k in (update.get("$unset") or {}):
                    d.pop(k, None)

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
        self.cto_projects = _FakeColl()
        self.finding_fixes = _FakeColl()
        self.fix_batches = _FakeColl()
        self.cto_open_findings = _FakeColl()
        self.fixed_findings = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


def _mock_gate(monkeypatch, fp, user_id="u1", is_admin=False):
    async def _fake_gate(authorization, level, db=None):
        caller = {"user_id": user_id, "is_admin": is_admin}
        return caller, {"user_id": user_id, "is_admin": is_admin}
    monkeypatch.setattr(fp, "_gate", _fake_gate)


def _mock_rate_limit(monkeypatch, fp, allowed=True):
    async def _fake_rl(key, limit):
        return allowed
    monkeypatch.setattr(fp, "check_rate_limit_async", _fake_rl)


def _mock_token(monkeypatch):
    async def _get_token(proj):
        return "ghp_tok", None, None
    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error", _get_token)


class TestRevertOneFixEndpoint:
    async def test_happy_path_reverts_and_returns_ok(self, monkeypatch):
        import routers.fix_pipeline as fp

        db = _FakeDB()
        db.finding_fixes.docs.append({
            "fix_id": "fx_1", "user_id": "u1", "project_id": "p1",
            "batch_id": "fb_1", "file": "a.py", "rule_id": "r1",
            "branch": "aurem/fixes-aaaaaaaa",
            "parent_commit_sha": "parent_a", "commit_sha": "after_a",
        })
        db.cto_projects.docs.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "o", "github_repo": "r",
        })
        _mock_gate(monkeypatch, fp)
        _mock_rate_limit(monkeypatch, fp, allowed=True)
        _mock_token(monkeypatch)
        monkeypatch.setattr(fp, "get_db", lambda: db)

        async def _fake_revert(db_, *, fix_id, token, user_id="", project_id=""):
            assert fix_id == "fx_1"
            assert user_id == "u1"
            assert project_id == "p1"
            return {"ok": True, "reverted": True, "commit_sha": "r1"}
        monkeypatch.setattr(fp, "revert_finding_fix", _fake_revert)

        result = await fp.revert_one_fix("fx_1", authorization="Bearer x")
        assert result["ok"] is True
        assert result["reverted"] is True

    async def test_foreign_fix_id_is_denied_as_not_found(self, monkeypatch):
        """A fix_id that exists but belongs to a DIFFERENT user must
        be denied exactly like a nonexistent one — never leaking
        whether the fix_id exists at all."""
        import routers.fix_pipeline as fp

        db = _FakeDB()
        db.finding_fixes.docs.append({
            "fix_id": "fx_other", "user_id": "someone_else", "project_id": "p1",
        })
        _mock_gate(monkeypatch, fp, user_id="u1")
        _mock_rate_limit(monkeypatch, fp, allowed=True)
        monkeypatch.setattr(fp, "get_db", lambda: db)

        with pytest.raises(HTTPException) as exc:
            await fp.revert_one_fix("fx_other", authorization="Bearer x")
        assert exc.value.status_code == 404

    async def test_already_reverted_fix_is_idempotent(self, monkeypatch):
        import routers.fix_pipeline as fp

        db = _FakeDB()
        db.finding_fixes.docs.append({
            "fix_id": "fx_1", "user_id": "u1", "project_id": "p1",
        })
        db.cto_projects.docs.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "o", "github_repo": "r",
        })
        _mock_gate(monkeypatch, fp)
        _mock_rate_limit(monkeypatch, fp, allowed=True)
        _mock_token(monkeypatch)
        monkeypatch.setattr(fp, "get_db", lambda: db)

        async def _fake_revert(db_, *, fix_id, token, user_id="", project_id=""):
            return {"ok": True, "already_reverted": True}
        monkeypatch.setattr(fp, "revert_finding_fix", _fake_revert)

        result = await fp.revert_one_fix("fx_1", authorization="Bearer x")
        assert result["ok"] is True
        assert result["already_reverted"] is True

    async def test_rate_limited_returns_429(self, monkeypatch):
        import routers.fix_pipeline as fp

        _mock_gate(monkeypatch, fp)
        _mock_rate_limit(monkeypatch, fp, allowed=False)

        with pytest.raises(HTTPException) as exc:
            await fp.revert_one_fix("fx_1", authorization="Bearer x")
        assert exc.value.status_code == 429


class TestRevertBatchEndpoint:
    async def test_requires_confirm_true_in_body(self, monkeypatch):
        import routers.fix_pipeline as fp

        _mock_gate(monkeypatch, fp)
        with pytest.raises(HTTPException) as exc:
            await fp.revert_one_batch("fb_1", body={}, authorization="Bearer x")
        assert exc.value.status_code == 400

    async def test_happy_path_with_confirm_reverts_the_batch(self, monkeypatch):
        import routers.fix_pipeline as fp

        db = _FakeDB()
        db.fix_batches.docs.append({
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "pr_number": 5, "status": "open",
        })
        db.cto_projects.docs.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "o", "github_repo": "r",
        })
        _mock_gate(monkeypatch, fp)
        _mock_rate_limit(monkeypatch, fp, allowed=True)
        _mock_token(monkeypatch)
        monkeypatch.setattr(fp, "get_db", lambda: db)

        async def _fake_revert_batch(db_, *, batch_id, user_id, project_id, token):
            assert batch_id == "fb_1"
            assert user_id == "u1"
            assert project_id == "p1"
            return {"ok": True, "pr_closed": True}
        monkeypatch.setattr(fp, "revert_batch", _fake_revert_batch)

        result = await fp.revert_one_batch(
            "fb_1", body={"confirm": True}, authorization="Bearer x",
        )
        assert result["ok"] is True
        assert result["pr_closed"] is True


class TestReopenFindingAfterRevert:
    async def test_revert_reopens_the_finding_and_drops_the_fixed_findings_ledger_row(self):
        """R6's bookkeeping contract: after a successful revert, the
        finding must come back — cto_open_findings flips to "open"
        and the fixed_findings ledger row (which would otherwise keep
        hiding it from the next rescan) is deleted."""
        from services.finding_fix_applier import _reopen_finding_after_revert
        from services.fixed_findings import finding_key

        db = _FakeDB()
        fix = {"user_id": "u1", "project_id": "p1", "rule_id": "r1",
               "file": "a.py", "line": 5}
        db.cto_open_findings.docs.append({
            "user_id": "u1", "project_id": "p1", "file": "a.py",
            "rule_id": "r1", "status": "fixed", "resolved_at": 123.0,
            "resolved_via": "rescan_clean",
        })
        key = finding_key({"rule_id": "r1", "file": "a.py", "line": 5})
        db.fixed_findings.docs.append({
            "user_id": "u1", "project_id": "p1", "key": key,
        })

        await _reopen_finding_after_revert(db, fix=fix)

        reopened = db.cto_open_findings.docs[0]
        assert reopened["status"] == "open"
        assert reopened["reopened_reason"] == "reverted"
        assert "resolved_at" not in reopened
        assert db.fixed_findings.docs == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
