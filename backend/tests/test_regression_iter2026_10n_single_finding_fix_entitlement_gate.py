"""tests/test_regression_iter2026_10n_single_finding_fix_entitlement_gate.py

Closing-the-bypass-doors audit — Step 1.

Two more single-finding "Fix" buttons call `apply_finding_fix()` — the
exact same worker `/fix-pipeline/bulk` uses — with no fix_entitlement
check at all, only the pre-existing task-quota tier gate
(`assert_can_fix`):

  - routers/codebase_health.py::request_fix     (POST /fix)
  - routers/security_scan.py::apply_security_fix (POST /fix)

Both are called directly as plain async functions here (the existing
test_iter212m114_real_finding_fix.py suite already establishes this
pattern), with every pre-existing gate (RBAC/team_permissions,
assert_can_fix tier gate) clearing naturally via a minimal FakeDB, so
the only thing under test is the NEW fix_entitlement gate.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_global_db():
    yield
    from cto_services import db as _dbmod
    _dbmod.set_db(None)


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query=None, projection=None, sort=None):
        query = query or {}
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        class _R:
            matched_count = 0
            modified_count = 0
        return _R()

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "fake"
        return _R()

    async def count_documents(self, query):
        return 0

    def find(self, query=None, projection=None, sort=None, limit=None):
        class _Cur:
            async def to_list(self, length=None):
                return []
        return _Cur()

    def aggregate(self, pipeline):
        class _AggCur:
            async def to_list(self, length=None):
                return []
        return _AggCur()


class _FakeDB:
    def __init__(self, *, user_id: str, entitled: bool, tier: str = "team",
                 tokens_remaining: int = 500):
        self.dev_users = _FakeColl([{
            "user_id": user_id, "tier": tier, "tokens_remaining": tokens_remaining,
            **({"fix_entitlement": True} if entitled else {}),
        }])

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll


# ─── codebase_health.py::request_fix (health-scan Fix button) ────────

class TestCodebaseHealthFixEntitlementGate:
    async def test_on_mode_402_when_not_entitled(self, monkeypatch):
        from routers import codebase_health as ch
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db = _FakeDB(user_id="u1", entitled=False)

        async def fake_current_dev(auth=None):
            return {"user_id": "u1", "tier": "pro",
                    "is_admin": False, "is_unlimited": False}
        monkeypatch.setattr(ch, "current_dev", fake_current_dev)
        monkeypatch.setattr(ch, "get_db", lambda: fake_db)
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)

        with pytest.raises(HTTPException) as exc_info:
            await ch.request_fix(
                body={"project_id": "p1", "finding_id": "f1", "file": "x.py", "line": 1},
                authorization="Bearer x",
            )
        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["code"] == "payment_required"

    async def test_on_mode_200_when_entitled(self, monkeypatch):
        from routers import codebase_health as ch
        from services import finding_fix_applier as ff
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db = _FakeDB(user_id="u1", entitled=True)

        async def fake_current_dev(auth=None):
            return {"user_id": "u1", "tier": "pro",
                    "is_admin": False, "is_unlimited": False}
        monkeypatch.setattr(ch, "current_dev", fake_current_dev)
        monkeypatch.setattr(ch, "get_db", lambda: fake_db)
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)

        async def fake_apply(*, db, user, project_id, finding):
            return {"ok": True, "commit_sha": "abc1234", "full_sha": "abc" * 10,
                    "html_url": "https://github.com/o/r/commit/abc1234",
                    "file": finding["file"], "rule_id": finding["rule_id"],
                    "message": "Fixed"}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        res = await ch.request_fix(
            body={"project_id": "p1", "finding_id": "f1", "file": "x.py", "line": 1},
            authorization="Bearer x",
        )
        assert res["ok"] is True

    async def test_off_mode_unaffected_default_behavior(self, monkeypatch):
        from routers import codebase_health as ch
        from services import finding_fix_applier as ff
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        fake_db = _FakeDB(user_id="u1", entitled=False)

        async def fake_current_dev(auth=None):
            return {"user_id": "u1", "tier": "pro",
                    "is_admin": False, "is_unlimited": False}
        monkeypatch.setattr(ch, "current_dev", fake_current_dev)
        monkeypatch.setattr(ch, "get_db", lambda: fake_db)
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)

        async def fake_apply(*, db, user, project_id, finding):
            return {"ok": True, "commit_sha": "abc1234", "full_sha": "abc" * 10,
                    "html_url": "https://github.com/o/r/commit/abc1234",
                    "file": finding["file"], "rule_id": finding["rule_id"],
                    "message": "Fixed"}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        res = await ch.request_fix(
            body={"project_id": "p1", "finding_id": "f1", "file": "x.py", "line": 1},
            authorization="Bearer x",
        )
        assert res["ok"] is True


# ─── security_scan.py::apply_security_fix (Vanguard Fix button) ──────

class TestSecurityScanFixEntitlementGate:
    async def test_on_mode_402_when_not_entitled(self, monkeypatch):
        from routers import security_scan as ss
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db = _FakeDB(user_id="u1", entitled=False)

        async def fake_current_dev(auth=None):
            return {"user_id": "u1", "tier": "starter"}
        monkeypatch.setattr(ss, "current_dev", fake_current_dev)
        monkeypatch.setattr(ss, "get_db", lambda: fake_db)
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)

        with pytest.raises(HTTPException) as exc_info:
            await ss.apply_security_fix(
                body={"project_id": "p1",
                      "finding": {"rule_id": "secret_aws_access_key", "file": "app.py"}},
                authorization="Bearer x",
            )
        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["code"] == "payment_required"

    async def test_on_mode_200_when_entitled(self, monkeypatch):
        from routers import security_scan as ss
        from services import finding_fix_applier as ff
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db = _FakeDB(user_id="u1", entitled=True)

        async def fake_current_dev(auth=None):
            return {"user_id": "u1", "tier": "starter"}
        monkeypatch.setattr(ss, "current_dev", fake_current_dev)
        monkeypatch.setattr(ss, "get_db", lambda: fake_db)
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)

        async def fake_apply(*, db, user, project_id, finding):
            return {"ok": True, "commit_sha": "abc1234", "full_sha": "abc" * 10,
                    "html_url": "https://github.com/o/r/commit/abc1234",
                    "file": finding["file"], "rule_id": finding["rule_id"],
                    "message": "Fixed"}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        res = await ss.apply_security_fix(
            body={"project_id": "p1",
                  "finding": {"rule_id": "secret_aws_access_key", "file": "app.py"}},
            authorization="Bearer x",
        )
        assert res["ok"] is True

    async def test_off_mode_unaffected_default_behavior(self, monkeypatch):
        from routers import security_scan as ss
        from services import finding_fix_applier as ff
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        fake_db = _FakeDB(user_id="u1", entitled=False)

        async def fake_current_dev(auth=None):
            return {"user_id": "u1", "tier": "starter"}
        monkeypatch.setattr(ss, "current_dev", fake_current_dev)
        monkeypatch.setattr(ss, "get_db", lambda: fake_db)
        from cto_services import db as _dbmod
        _dbmod.set_db(fake_db)

        async def fake_apply(*, db, user, project_id, finding):
            return {"ok": True, "commit_sha": "abc1234", "full_sha": "abc" * 10,
                    "html_url": "https://github.com/o/r/commit/abc1234",
                    "file": finding["file"], "rule_id": finding["rule_id"],
                    "message": "Fixed"}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        res = await ss.apply_security_fix(
            body={"project_id": "p1",
                  "finding": {"rule_id": "secret_aws_access_key", "file": "app.py"}},
            authorization="Bearer x",
        )
        assert res["ok"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
