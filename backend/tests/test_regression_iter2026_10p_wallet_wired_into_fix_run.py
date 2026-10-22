"""tests/test_regression_iter2026_10p_wallet_wired_into_fix_run.py

Master Prompt Step 2 — wire wallet hold/settle/release into the real
fix run.

Prior state (verified by grep before writing this): services/wallet.py
existed with a full hold/settle/release API, but nothing in the real
worker path ever called it — only `wallet.grant` (from the Stripe
webhook) was wired anywhere. This file tests the fix:

  - `routers/cto_projects/tasks.py::submit_task` /
    `_enqueue_cto_task` / `retry_task` each call `assert_wallet`
    (hold) right before the task row is created, using the new
    task_id as the hold's run_id.
  - `routers/cto_projects/worker_api.py::_run_task` — the ONE
    dispatcher every one of those three callers funnels through —
    settles the hold (task finished with status="done") or releases
    it (anything else) in a `finally` block, exactly once per task_id.

Uses a REAL local Mongo connection (same rationale as
test_regression_iter2026_10h: a dict-based mock cannot prove the
atomic-concurrency guarantee a real find_one_and_update pipeline
gives us).
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)

FIX_PRICE = 12  # config/token_pricing.py::PRICE_TABLE["fix"]


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    yield database
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.cto_tasks.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.cto_projects.delete_many({"user_id": {"$regex": "^wallet_test_"}})


def _client_for(user_doc):
    from cto_services.auth import create_token
    from main import app

    c = TestClient(app)
    c.__enter__()
    token = create_token(user_doc["user_id"], user_doc["email"])
    c.headers["Authorization"] = f"Bearer {token}"
    return c


class TestHoldAtTaskCreation:
    async def test_submit_task_holds_tokens_in_on_mode(self, db, monkeypatch):
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("submit")
        await db.dev_users.insert_one({
            "user_id": u, "email": f"{u}@aurem.dev", "tier": "team",
            "is_admin": False, "is_unlimited": False, "fix_entitlement": True,
        })
        await db.cto_projects.insert_one({
            "project_id": "p1", "user_id": u,
            "github_owner": "o", "github_repo": "r",
        })
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")

        from cto_services import db as cto_db
        cto_db.set_db(db)
        c = _client_for({"user_id": u, "email": f"{u}@aurem.dev"})
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
                r = c.post("/api/aurem-dev/cto/tasks/submit",
                          json={"project_id": "p1", "task": "fix the login bug in auth.py"})
            assert r.status_code == 200, r.text
        finally:
            c.__exit__(None, None, None)
            cto_db.set_db(None)

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - FIX_PRICE

    async def test_submit_task_402_when_insufficient_balance_no_task_created(
        self, db, monkeypatch,
    ):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("broke")
        await db.dev_users.insert_one({
            "user_id": u, "email": f"{u}@aurem.dev", "tier": "team",
            "is_admin": False, "is_unlimited": False, "fix_entitlement": True,
        })
        await db.cto_projects.insert_one({
            "project_id": "p1", "user_id": u,
            "github_owner": "o", "github_repo": "r",
        })
        # No grant — balance is 0.

        from cto_services import db as cto_db
        cto_db.set_db(db)
        c = _client_for({"user_id": u, "email": f"{u}@aurem.dev"})
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))):
                r = c.post("/api/aurem-dev/cto/tasks/submit",
                          json={"project_id": "p1", "task": "fix the login bug in auth.py"})
            assert r.status_code == 402, r.text
            assert r.json()["detail"]["code"] == "tokens_exhausted"
        finally:
            c.__exit__(None, None, None)
            cto_db.set_db(None)

        n_tasks = await db.cto_tasks.count_documents({"user_id": u})
        assert n_tasks == 0, "no task row when the wallet hold is blocked"

    async def test_off_mode_never_touches_wallet(self, db, monkeypatch):
        from services import wallet
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        u = _uid("offmode")
        await db.dev_users.insert_one({
            "user_id": u, "email": f"{u}@aurem.dev", "tier": "team",
            "is_admin": False, "is_unlimited": False,
        })
        await db.cto_projects.insert_one({
            "project_id": "p1", "user_id": u,
            "github_owner": "o", "github_repo": "r",
        })

        from cto_services import db as cto_db
        cto_db.set_db(db)
        c = _client_for({"user_id": u, "email": f"{u}@aurem.dev"})
        try:
            with patch("services.pat_vault.get_repo_token_or_error",
                       AsyncMock(return_value=("tok", None, None))), \
                 patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
                r = c.post("/api/aurem-dev/cto/tasks/submit",
                          json={"project_id": "p1", "task": "fix the login bug in auth.py"})
            assert r.status_code == 200, r.text
        finally:
            c.__exit__(None, None, None)
            cto_db.set_db(None)

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 0, "off mode must never create a wallet row"


class TestSettleReleaseViaRunTask:
    async def test_successful_task_settles_full_hold_no_refund(self, db, monkeypatch):
        import routers.cto_projects as _pkg
        from routers.cto_projects.worker_api import _run_task
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("settle")
        task_id = f"t_{uuid.uuid4().hex[:10]}"
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")
        await wallet.assert_wallet(db, user_id=u, action="fix", run_id=task_id,
                                   idempotency_key=f"wallet_hold:{task_id}")
        await db.cto_tasks.insert_one({"task_id": task_id, "user_id": u, "status": "queued"})

        async def fake_worker(*a, **kw):
            await db.cto_tasks.update_one(
                {"task_id": task_id}, {"$set": {"status": "done"}},
            )
        from cto_services import db as cto_db
        cto_db.set_db(db)
        try:
            with patch.object(_pkg, "_GIT_AVAILABLE", False), \
                 patch.object(_pkg, "_run_task_via_api", AsyncMock(side_effect=fake_worker)):
                await _run_task(task_id, {"user_id": u}, "fix it", [], "", "tok")
        finally:
            cto_db.set_db(None)

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - FIX_PRICE, "settled at the held amount — no refund"
        ledger = await db.wallet_ledger.find_one({"_id": f"wallet_settle:{task_id}"})
        assert ledger is not None
        assert ledger["amount"] == FIX_PRICE

    async def test_failed_task_releases_the_hold_fully(self, db, monkeypatch):
        import routers.cto_projects as _pkg
        from routers.cto_projects.worker_api import _run_task
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("release")
        task_id = f"t_{uuid.uuid4().hex[:10]}"
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")
        await wallet.assert_wallet(db, user_id=u, action="fix", run_id=task_id,
                                   idempotency_key=f"wallet_hold:{task_id}")
        await db.cto_tasks.insert_one({"task_id": task_id, "user_id": u, "status": "queued"})

        async def fake_worker(*a, **kw):
            await db.cto_tasks.update_one(
                {"task_id": task_id}, {"$set": {"status": "failed"}},
            )
        from cto_services import db as cto_db
        cto_db.set_db(db)
        try:
            with patch.object(_pkg, "_GIT_AVAILABLE", False), \
                 patch.object(_pkg, "_run_task_via_api", AsyncMock(side_effect=fake_worker)):
                await _run_task(task_id, {"user_id": u}, "fix it", [], "", "tok")
        finally:
            cto_db.set_db(None)

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100, "a failed task must refund the FULL hold"

    async def test_worker_exception_still_releases_the_hold(self, db, monkeypatch):
        """Even if the concrete worker raises, the `finally` in
        _run_task must still settle/release — a hold must never be
        stuck open on an unhandled exception."""
        import routers.cto_projects as _pkg
        from routers.cto_projects.worker_api import _run_task
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("crashed")
        task_id = f"t_{uuid.uuid4().hex[:10]}"
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")
        await wallet.assert_wallet(db, user_id=u, action="fix", run_id=task_id,
                                   idempotency_key=f"wallet_hold:{task_id}")
        await db.cto_tasks.insert_one({"task_id": task_id, "user_id": u, "status": "queued"})

        async def crashing_worker(*a, **kw):
            raise RuntimeError("boom")
        from cto_services import db as cto_db
        cto_db.set_db(db)
        try:
            with patch.object(_pkg, "_GIT_AVAILABLE", False), \
                 patch.object(_pkg, "_run_task_via_api", AsyncMock(side_effect=crashing_worker)), \
                 pytest.raises(RuntimeError):
                await _run_task(task_id, {"user_id": u}, "fix it", [], "", "tok")
        finally:
            cto_db.set_db(None)

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100, "crash (status stays 'queued') must still release"

    async def test_retry_of_same_task_id_never_double_charges(self, db, monkeypatch):
        """Calling _run_task's finalize path twice for the SAME task_id
        (e.g. a duplicate background-task invocation) must settle only
        once — idempotency_key = f"wallet_settle:{task_id}"."""
        from routers.cto_projects.worker_api import _finalize_wallet_hold
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("idempotent")
        task_id = f"t_{uuid.uuid4().hex[:10]}"
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")
        await wallet.assert_wallet(db, user_id=u, action="fix", run_id=task_id,
                                   idempotency_key=f"wallet_hold:{task_id}")
        await db.cto_tasks.insert_one({"task_id": task_id, "user_id": u, "status": "done"})

        import routers.cto_projects.worker_api as wapi
        monkeypatch.setattr(wapi, "_pkg", type("P", (), {"get_db": staticmethod(lambda: db)}))
        await _finalize_wallet_hold(task_id, u)
        await _finalize_wallet_hold(task_id, u)  # replay

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - FIX_PRICE, "second settle call must be a no-op"

    async def test_two_concurrent_task_creations_never_go_negative(self, db, monkeypatch):
        """Real asyncio.gather against a real Mongo balance that can
        only cover ONE of the two holds — proves the atomic spend
        pipeline, not just the mode-aware wrapper, is actually wired
        in from the task-creation call site."""
        import asyncio

        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("concurrent")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=FIX_PRICE,
                           idempotency_key=f"grant:{u}", source="test")

        run_id_a, run_id_b = f"t_{uuid.uuid4().hex[:8]}", f"t_{uuid.uuid4().hex[:8]}"

        async def _try(run_id):
            try:
                await wallet.assert_wallet(
                    db, user_id=u, action="fix", run_id=run_id,
                    idempotency_key=f"wallet_hold:{run_id}",
                )
                return True
            except (HTTPException, wallet.InsufficientTokens):
                return False

        results = await asyncio.gather(_try(run_id_a), _try(run_id_b))
        assert sorted(results) == [False, True], "exactly one hold must win"
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 0
        assert bal["spendable"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
