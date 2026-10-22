"""tests/test_regression_iter2026_10l_enqueue_and_retry_entitlement_gate.py

Closing-the-bypass-doors audit — Step 1.

Two more real, user-triggered doors into the paid fix worker had no
fix_entitlement check at all:

  1. `routers/cto_projects/tasks.py::_enqueue_cto_task` — the shared
     enqueue path for MCP's `ship_code` tool (`routers/mcp.py::
     _tool_ship_code`) and both automation triggers (push webhook +
     manual run-now, `routers/automations.py`). Gated once, at the
     function itself, so every current/future caller is covered.
  2. `routers/cto_projects/tasks.py::retry_task` (`POST /tasks/{id}/
     retry`) — re-queues a brand-new task through the exact same
     worker as `/tasks/submit`.

This file proves:
  - `_enqueue_cto_task` is off/shadow/on-aware and returns
    {"ok": False, "reason": "payment_required"} (never raises — it
    must stay a plain dict-returning function since the push-webhook
    caller loops over multiple rules and must be able to skip one
    unentitled rule without crashing the whole delivery).
  - MCP's `_tool_ship_code` can no longer bypass the gate: calling it
    directly while unentitled raises (no silent queue), and critically
    NO `cto_tasks` row is created.
  - `retry_task` 402s in "on" when not entitled, no-ops in "off",
    and passes through when entitled.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


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
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                class _R:
                    matched_count = 1
                    modified_count = 1
                return _R()
        class _R:
            matched_count = 0
            modified_count = 0
        return _R()

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "fake"
        return _R()

    def aggregate(self, pipeline):
        class _AggCur:
            async def to_list(self, length=None):
                return []
        return _AggCur()

    async def count_documents(self, query):
        return 0


class _FakeDB:
    def __init__(self, user_doc=None, projects=None, tasks=None):
        self.dev_users = _FakeColl([user_doc] if user_doc else [])
        self.cto_projects = _FakeColl(projects or [])
        self.cto_tasks = _FakeColl(tasks or [])

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll


# ─── _enqueue_cto_task direct unit tests ───────────────────────────────

@pytest.mark.asyncio
class TestEnqueueCtoTaskEntitlementGate:
    async def test_on_mode_blocks_when_not_entitled_no_task_created(self, monkeypatch):
        from cto_services import db as _dbmod
        from routers.cto_projects import _enqueue_cto_task
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db = _FakeDB(
            user_doc={"user_id": "u1"},
            projects=[{"project_id": "p1", "user_id": "u1"}],
        )
        _dbmod.set_db(fake_db)
        try:
            result = await _enqueue_cto_task("u1", "p1", "fix it")
        finally:
            _dbmod.set_db(None)
        assert result == {"ok": False, "reason": "payment_required"}
        assert fake_db.cto_tasks.rows == [], "no task row when blocked"

    async def test_on_mode_allows_when_entitled(self, monkeypatch):
        from cto_services import db as _dbmod
        from routers.cto_projects import _enqueue_cto_task
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db = _FakeDB(
            user_doc={"user_id": "u1", "fix_entitlement": True},
            projects=[{"project_id": "p1", "user_id": "u1"}],
        )
        _dbmod.set_db(fake_db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)), \
                 patch("services.wallet.assert_wallet", AsyncMock(return_value={"mode": "on", "held": True})):
                result = await _enqueue_cto_task("u1", "p1", "fix it")
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is True
        assert len(fake_db.cto_tasks.rows) == 1

    async def test_shadow_mode_never_blocks(self, monkeypatch):
        from cto_services import db as _dbmod
        from routers.cto_projects import _enqueue_cto_task
        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        fake_db = _FakeDB(
            user_doc={"user_id": "u1"},
            projects=[{"project_id": "p1", "user_id": "u1"}],
        )
        _dbmod.set_db(fake_db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
                result = await _enqueue_cto_task("u1", "p1", "fix it")
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is True, "shadow mode must never block"

    async def test_off_mode_default_behavior_unaffected(self, monkeypatch):
        from cto_services import db as _dbmod
        from routers.cto_projects import _enqueue_cto_task
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        fake_db = _FakeDB(
            user_doc={"user_id": "u1"},
            projects=[{"project_id": "p1", "user_id": "u1"}],
        )
        _dbmod.set_db(fake_db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
                result = await _enqueue_cto_task("u1", "p1", "fix it")
        finally:
            _dbmod.set_db(None)
        assert result["ok"] is True


# ─── MCP ship_code can no longer bypass /tasks/submit's gate ──────────

@pytest.mark.asyncio
class TestMcpShipCodeCannotBypass:
    async def test_ship_code_blocked_when_not_entitled(self, monkeypatch):
        from cto_services import db as _dbmod
        from routers.mcp import _tool_ship_code
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db = _FakeDB(
            user_doc={"user_id": "u1"},
            projects=[{"project_id": "p1", "user_id": "u1"}],
        )
        _dbmod.set_db(fake_db)
        try:
            with pytest.raises(RuntimeError, match="payment_required"):
                await _tool_ship_code("u1", {
                    "task": "fix the signup bug please", "project_id": "p1",
                })
        finally:
            _dbmod.set_db(None)
        assert fake_db.cto_tasks.rows == [], (
            "MCP ship_code must not be able to queue a task when unentitled"
        )

    async def test_ship_code_works_when_entitled(self, monkeypatch):
        from cto_services import db as _dbmod
        from routers.mcp import _tool_ship_code
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db = _FakeDB(
            user_doc={"user_id": "u1", "fix_entitlement": True},
            projects=[{"project_id": "p1", "user_id": "u1"}],
        )
        _dbmod.set_db(fake_db)
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)), \
                 patch("services.wallet.assert_wallet", AsyncMock(return_value={"mode": "on", "held": True})):
                result = await _tool_ship_code("u1", {
                    "task": "fix the signup bug please", "project_id": "p1",
                })
        finally:
            _dbmod.set_db(None)
        assert result["status"] == "queued"
        assert len(fake_db.cto_tasks.rows) == 1


# ─── retry_task HTTP-level gate ─────────────────────────────────────────

def _client_for(user_doc, projects=None, tasks=None):
    from cto_services import db as cto_db
    from cto_services.auth import create_token
    from main import app

    db = _FakeDB(user_doc=user_doc, projects=projects, tasks=tasks)
    c = TestClient(app)
    c.__enter__()
    cto_db.set_db(db)
    token = create_token(user_doc["user_id"], user_doc["email"])
    c.headers["Authorization"] = f"Bearer {token}"
    return c, db


_OLD_TASK = {
    "task_id": "t_old1", "user_id": "u_retry", "project_id": "p1",
    "task": "fix it", "files": [], "context": "", "status": "failed",
    "error": "boom", "steps": [], "created_at": time.time() - 100,
}
_PROJECT = {"project_id": "p1", "user_id": "u_retry",
            "github_owner": "o", "github_repo": "r"}


class TestRetryTaskEntitlementGate:
    def test_on_mode_402_when_not_entitled(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        c, db = _client_for(
            {"user_id": "u_retry", "email": "r@aurem.dev", "tier": "team",
             "is_admin": False, "is_unlimited": False},
            projects=[_PROJECT], tasks=[dict(_OLD_TASK)],
        )
        try:
            r = c.post("/api/aurem-dev/cto/tasks/t_old1/retry")
            assert r.status_code == 402, r.text
            assert r.json()["detail"]["code"] == "payment_required"
            assert len(db.cto_tasks.rows) == 1, "no new task row when blocked"
        finally:
            c.__exit__(None, None, None)

    def test_on_mode_200_when_entitled(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        c, db = _client_for(
            {"user_id": "u_retry", "email": "r@aurem.dev", "tier": "team",
             "is_admin": False, "is_unlimited": False, "fix_entitlement": True},
            projects=[_PROJECT], tasks=[dict(_OLD_TASK)],
        )
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)), \
                 patch("services.wallet.assert_wallet", AsyncMock(return_value={"mode": "on", "held": True})):
                r = c.post("/api/aurem-dev/cto/tasks/t_old1/retry")
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True
            assert len(db.cto_tasks.rows) == 2, "a new retry task row was created"
        finally:
            c.__exit__(None, None, None)

    def test_off_mode_unaffected_default_behavior(self, monkeypatch):
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        c, _db = _client_for(
            {"user_id": "u_retry", "email": "r@aurem.dev", "tier": "team",
             "is_admin": False, "is_unlimited": False},
            projects=[_PROJECT], tasks=[dict(_OLD_TASK)],
        )
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
                r = c.post("/api/aurem-dev/cto/tasks/t_old1/retry")
            assert r.status_code == 200, r.text
        finally:
            c.__exit__(None, None, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
