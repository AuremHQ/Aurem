"""tests/test_regression_iter2026_10k_fix_pipeline_entitlement_gate.py

Master Prompt Step C2 — POST /fix-pipeline/bulk (the bulk "Fix"
button's HTTP entry point) is gated by services.fix_entitlement
exactly like /cto/tasks/submit. Reuses the same `_FakeDB`-via-
`main.app` TestClient pattern already established in
tests/test_iter212m121_fix_pipeline.py (its `__getattr__` auto-creates
an empty fake collection for `db.wallets`/`db.dev_users` etc., so this
needs no new fixture infra).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query, projection=None):
        for r in self.rows:
            ok = all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict))
            if ok:
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                for k, v in (update.get("$inc") or {}).items():
                    r[k] = (r.get(k) or 0) + v
                class _R:
                    matched_count = 1
                    modified_count = 1
                    upserted_id = None
                return _R()
        class _R:
            matched_count = 0
            modified_count = 0
            upserted_id = None
        return _R()

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "fake"
        return _R()

    def aggregate(self, pipeline):
        class _AggCur:
            def __init__(self, items):
                self.items = items
            async def to_list(self, length=None):
                return list(self.items)[: (length or len(self.items))]
            def __aiter__(self):
                async def _gen():
                    for x in list(self.items):
                        yield x
                return _gen()
        return _AggCur([])

    async def count_documents(self, query):
        return 0


class _FakeDB:
    def __init__(self, user_doc=None, projects=None):
        self.dev_users = _FakeColl([user_doc] if user_doc else [])
        self.cto_projects = _FakeColl(projects or [])
        self.finding_fixes = _FakeColl()
        self.cto_tasks = _FakeColl()
        self.vanguard_ci_findings = _FakeColl()
        self.scan_fix_usage = _FakeColl()
        self.scan_fix_jobs = _FakeColl()
        self.topup_alerts = _FakeColl()
        self.incidents = _FakeColl()
        self.loop_events = _FakeColl()

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll


def _client_for(user_doc):
    from cto_services import db as cto_db
    from cto_services.auth import create_token
    from main import app

    db = _FakeDB(user_doc=user_doc)
    c = TestClient(app)
    c.__enter__()
    cto_db.set_db(db)
    token = create_token(user_doc["user_id"], user_doc["email"])
    c.headers["Authorization"] = f"Bearer {token}"
    return c, db


_FINDINGS = [
    {"id": "f1", "rule_id": "secret_aws_key", "file": "config.py", "line": 12,
     "vuln": "secret_leak", "severity": "critical", "category": "vanguard"},
]


class TestFixPipelineBulkEntitlementGate:
    def test_on_mode_402_when_not_entitled(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        c, db = _client_for({
            "user_id": "u_notentitled", "email": "ne@aurem.dev",
            "tokens_remaining": 100, "tier": "team",
            "is_admin": False, "is_unlimited": False,
        })
        try:
            r = c.post("/api/aurem-dev/fix-pipeline/bulk",
                       json={"project_id": "p1", "findings": _FINDINGS})
            assert r.status_code == 402, r.text
            assert r.json()["detail"]["code"] == "payment_required"
            assert db.cto_tasks.rows == []
            assert db.scan_fix_jobs.rows == [], "no job must be created when not entitled"
        finally:
            c.__exit__(None, None, None)

    def test_on_mode_200_when_entitled(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        c, _db = _client_for({
            "user_id": "u_entitled", "email": "e@aurem.dev",
            "tokens_remaining": 100, "tier": "team",
            "is_admin": False, "is_unlimited": False,
            "fix_entitlement": True,
        })
        try:
            async def fake_apply(**kw):
                return {"ok": True, "commit_sha": "abc1234",
                        "full_sha": "abc1234" + "0" * 33,
                        "html_url": "https://github.com/x/y/commit/abc1234",
                        "file": kw["finding"].get("file"),
                        "rule_id": kw["finding"].get("rule_id"),
                        "message": "stub"}

            async def _verify_ok(**kw):
                return True

            monkeypatch.setattr("routers.fix_pipeline.apply_finding_fix", fake_apply)
            monkeypatch.setattr("routers.fix_pipeline._verify_commit_exists", _verify_ok)
            # Wallet wiring (Master Prompt v2 Part 1.1) — this FakeDB
            # has no real wallet balance; mock the gate so this test
            # stays focused on entitlement (wallet has its own
            # dedicated test coverage in test_regression_iter2026_10y).
            monkeypatch.setattr("services.wallet.assert_wallet",
                                AsyncMock(return_value={"mode": "on", "held": True}))
            monkeypatch.setattr("services.wallet.finalize_hold", AsyncMock(return_value=None))

            r = c.post("/api/aurem-dev/fix-pipeline/bulk",
                       json={"project_id": "p1", "findings": _FINDINGS})
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True
        finally:
            c.__exit__(None, None, None)

    def test_off_mode_unaffected_default_behavior(self, monkeypatch):
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        c, _db = _client_for({
            "user_id": "u_offmode", "email": "off@aurem.dev",
            "tokens_remaining": 100, "tier": "team",
            "is_admin": False, "is_unlimited": False,
        })
        try:
            async def fake_apply(**kw):
                return {"ok": True, "commit_sha": "abc1234",
                        "full_sha": "abc1234" + "0" * 33,
                        "html_url": "https://github.com/x/y/commit/abc1234",
                        "file": kw["finding"].get("file"),
                        "rule_id": kw["finding"].get("rule_id"),
                        "message": "stub"}

            async def _verify_ok(**kw):
                return True

            monkeypatch.setattr("routers.fix_pipeline.apply_finding_fix", fake_apply)
            monkeypatch.setattr("routers.fix_pipeline._verify_commit_exists", _verify_ok)

            r = c.post("/api/aurem-dev/fix-pipeline/bulk",
                       json={"project_id": "p1", "findings": _FINDINGS})
            assert r.status_code == 200, r.text
        finally:
            c.__exit__(None, None, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
