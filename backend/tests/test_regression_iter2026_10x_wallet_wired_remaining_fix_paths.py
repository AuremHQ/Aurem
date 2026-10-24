"""tests/test_regression_iter2026_10x_wallet_wired_remaining_fix_paths.py

Master Prompt v2 Part 1.1 — wallet hold/settle/release wired into every
remaining billable path that previously only had the fix_entitlement
gate: codebase_health.py::request_fix, security_scan.py::
apply_security_fix, mode_d_debugger.py's chat fix loop, and
loop.py::start_loop (loop run).

Uses a REAL local Mongo connection (same rationale as
test_regression_iter2026_10h/10p: wallet.py's atomic pipeline and
idempotent ledger dedup cannot be meaningfully proven against a
dict-based mock).
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)

FIX_PRICE = 12       # config/token_pricing.py::PRICE_TABLE["fix"]
LOOP_PRICE = 40       # config/token_pricing.py::PRICE_TABLE["loop_run"]


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
        await db.cto_projects.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.loop_sessions.delete_many({"user_id": {"$regex": "^wallet_test_"}})


class TestCodebaseHealthFixWalletWiring:
    async def test_success_settles_exactly_the_fix_price(self, db, monkeypatch):
        from routers import codebase_health as ch
        from services import finding_fix_applier as ff
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("healthok")
        await db.dev_users.insert_one({"user_id": u, "tier": "pro", "fix_entitlement": True, "tokens_remaining": 0})
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")

        async def fake_current_dev(auth=None):
            return {"user_id": u, "tier": "pro", "is_admin": False, "is_unlimited": False}
        monkeypatch.setattr(ch, "current_dev", fake_current_dev)
        monkeypatch.setattr(ch, "get_db", lambda: db)
        from cto_services import db as _dbmod
        _dbmod.set_db(db)

        async def fake_apply(*, db, user, project_id, finding):
            return {"ok": True, "commit_sha": "abc1234", "full_sha": "abc1234" * 10,
                    "html_url": "https://github.com/o/r/commit/abc1234",
                    "file": finding["file"],
                    "rule_id": finding["rule_id"], "message": "Fixed"}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        try:
            res = await ch.request_fix(
                body={"project_id": "p1", "finding_id": "f1", "file": "x.py", "line": 1},
                authorization="Bearer x",
            )
        finally:
            _dbmod.set_db(None)
        assert res["ok"] is True
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - FIX_PRICE

    async def test_failure_releases_the_hold_fully(self, db, monkeypatch):
        from routers import codebase_health as ch
        from services import finding_fix_applier as ff
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("healthfail")
        await db.dev_users.insert_one({"user_id": u, "tier": "pro", "fix_entitlement": True, "tokens_remaining": 0})
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")

        async def fake_current_dev(auth=None):
            return {"user_id": u, "tier": "pro", "is_admin": False, "is_unlimited": False}
        monkeypatch.setattr(ch, "current_dev", fake_current_dev)
        monkeypatch.setattr(ch, "get_db", lambda: db)
        from cto_services import db as _dbmod
        _dbmod.set_db(db)

        async def fake_apply(*, db, user, project_id, finding):
            return {"ok": False, "error": "patch_did_not_resolve_finding"}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        try:
            with pytest.raises(HTTPException) as exc_info:
                await ch.request_fix(
                    body={"project_id": "p1", "finding_id": "f1", "file": "x.py", "line": 1},
                    authorization="Bearer x",
                )
        finally:
            _dbmod.set_db(None)
        assert exc_info.value.status_code == 422
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100, "a failed fix must refund the FULL hold"

    async def test_insufficient_balance_blocks_with_typed_402_before_any_fix(
        self, db, monkeypatch,
    ):
        from routers import codebase_health as ch
        from services import finding_fix_applier as ff
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("healthbroke")
        await db.dev_users.insert_one({"user_id": u, "tier": "pro", "fix_entitlement": True, "tokens_remaining": 0})
        # No grant — balance is 0.

        async def fake_current_dev(auth=None):
            return {"user_id": u, "tier": "pro", "is_admin": False, "is_unlimited": False}
        monkeypatch.setattr(ch, "current_dev", fake_current_dev)
        monkeypatch.setattr(ch, "get_db", lambda: db)
        from cto_services import db as _dbmod
        _dbmod.set_db(db)

        called = {"apply": False}
        async def fake_apply(**kw):
            called["apply"] = True
            return {"ok": True}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        try:
            with pytest.raises(HTTPException) as exc_info:
                await ch.request_fix(
                    body={"project_id": "p1", "finding_id": "f1", "file": "x.py", "line": 1},
                    authorization="Bearer x",
                )
        finally:
            _dbmod.set_db(None)
        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["code"] == "tokens_exhausted"
        assert called["apply"] is False


class TestSecurityScanFixWalletWiring:
    async def test_success_settles_exactly_the_fix_price(self, db, monkeypatch):
        from routers import security_scan as ss
        from services import finding_fix_applier as ff
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("vgok")
        await db.dev_users.insert_one({"user_id": u, "tier": "team", "fix_entitlement": True, "tokens_remaining": 0})
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")

        async def fake_current_dev(auth=None):
            return {"user_id": u, "tier": "team"}
        monkeypatch.setattr(ss, "current_dev", fake_current_dev)
        monkeypatch.setattr(ss, "get_db", lambda: db)
        from cto_services import db as _dbmod
        _dbmod.set_db(db)


        async def fake_apply(*, db, user, project_id, finding):
            return {"ok": True, "commit_sha": "abc1234", "full_sha": "abc1234" * 10,
                    "html_url": "https://github.com/o/r/commit/abc1234",
                    "file": finding["file"],
                    "rule_id": finding["rule_id"], "message": "Fixed"}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        try:
            res = await ss.apply_security_fix(
                body={"project_id": "p1",
                      "finding": {"rule_id": "secret_aws_access_key", "file": "app.py"}},
                authorization="Bearer x",
            )
        finally:
            _dbmod.set_db(None)
        assert res["ok"] is True
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - FIX_PRICE


class TestChatFixLoopWalletWiring:
    async def test_second_finding_blocked_when_balance_only_covers_one(self, db, monkeypatch):
        """Two candidate findings, balance for exactly one fix — the
        first must succeed and settle, the second must be reported as
        a failed/blocked line, never silently skipped or double-held."""
        from services import finding_fix_applier as ff
        from services import wallet
        from services.mode_d_debugger import try_scan_followup_reply
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("chatfix")
        await db.dev_users.insert_one({"user_id": u, "tier": "team", "fix_entitlement": True, "tokens_remaining": 0})
        await wallet.grant(db, user_id=u, bucket="purchased", amount=FIX_PRICE,
                           idempotency_key=f"grant:{u}", source="test")
        from cto_services import db as _dbmod
        _dbmod.set_db(db)

        import time as _time
        scan_doc = {
            "user_id": u, "project_id": "p1",
            "created_at": _time.time(), "summary": "scan",
            "breakdown": {"security": {"findings": [
                {"id": "f1", "file": "a.py", "line": 1, "severity": "critical",
                 "rule_id": "r1", "title": "t1", "message": "m1", "fix_hint": "h1"},
                {"id": "f2", "file": "b.py", "line": 2, "severity": "critical",
                 "rule_id": "r2", "title": "t2", "message": "m2", "fix_hint": "h2"},
            ]}},
        }
        await db.codebase_health_scans.insert_one(scan_doc)

        async def fake_apply(**kw):
            return {"ok": True, "commit_sha": "abc1234",
                    "file": kw["finding"]["file"], "rule_id": kw["finding"]["rule_id"]}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        result = await try_scan_followup_reply(
            db, user_id=u, project_id="p1",
            user_message="please fix the critical findings from that scan",
        )
        assert result is not None
        assert "fixed 1 of 2" in result["ora_reply"].lower()
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 0, "exactly one fix's worth was ever spent"


class TestLoopRunWalletWiring:
    async def test_lock_contention_releases_the_hold(self, db, monkeypatch):
        from routers import loop as loop_router
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("loopcontend")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "team", "is_admin": False,
            "is_unlimited": False, "fix_entitlement": True,
        })
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")

        async def fake_current_dev(_auth):
            return {"user_id": u, "tier": "team", "is_admin": False, "is_unlimited": False}
        monkeypatch.setattr(loop_router, "current_dev", fake_current_dev)
        monkeypatch.setattr(loop_router, "get_db", lambda: db)

        from services import loop_safety
        async def fake_acquire(*_a, **_kw):
            return False, {"loop_id": "lp_existing"}
        monkeypatch.setattr(loop_safety, "acquire_loop_lock", fake_acquire)
        async def fake_circuit(*_a, **_kw):
            return (False, 0, 0)
        monkeypatch.setattr(loop_safety, "is_loop_circuit_open", fake_circuit)

        _fake_req = type("R", (), {
            "client": type("C", (), {"host": "127.0.0.1"})(),
            "headers": {},
            "url": type("U", (), {"path": "/api/aurem-dev/loop/start"})(),
        })()
        body = loop_router.StartBody(user_message="fix the login bug in auth.py")
        with pytest.raises(HTTPException) as exc_info:
            await loop_router.start_loop(body=body, request=_fake_req, authorization=None)
        assert exc_info.value.status_code == 409

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100, "lock contention must release the hold, not leak it"

    async def test_persist_session_settles_on_completed(self, db, monkeypatch):
        from services import wallet
        from services.loop_engine_helpers import _persist_session
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("loopdone")
        loop_id = f"lp_{uuid.uuid4().hex[:10]}"
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")
        await wallet.assert_wallet(db, user_id=u, action="loop_run", run_id=loop_id,
                                   idempotency_key=f"wallet_hold:{loop_id}")

        await _persist_session(db, {
            "loop_id": loop_id, "user_id": u, "project_id": "p1",
            "state": "completed", "phase": "ship", "context": {},
        })
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - LOOP_PRICE

    async def test_persist_session_releases_on_failed(self, db, monkeypatch):
        from services import wallet
        from services.loop_engine_helpers import _persist_session
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("loopfail")
        loop_id = f"lp_{uuid.uuid4().hex[:10]}"
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")
        await wallet.assert_wallet(db, user_id=u, action="loop_run", run_id=loop_id,
                                   idempotency_key=f"wallet_hold:{loop_id}")

        await _persist_session(db, {
            "loop_id": loop_id, "user_id": u, "project_id": "p1",
            "state": "failed", "phase": "execute", "context": {},
        })
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100, "a failed loop must refund the FULL hold"

    async def test_persist_session_noop_while_still_in_progress(self, db, monkeypatch):
        """A non-terminal state (e.g. still executing) must NOT settle
        or release — the run continues even if later checked again."""
        from services import wallet
        from services.loop_engine_helpers import _persist_session
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("loopinprogress")
        loop_id = f"lp_{uuid.uuid4().hex[:10]}"
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                           idempotency_key=f"grant:{u}", source="test")
        await wallet.assert_wallet(db, user_id=u, action="loop_run", run_id=loop_id,
                                   idempotency_key=f"wallet_hold:{loop_id}")

        await _persist_session(db, {
            "loop_id": loop_id, "user_id": u, "project_id": "p1",
            "state": "executing", "phase": "execute", "context": {},
        })
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - LOOP_PRICE, "hold still active, not refunded yet"
        # The run finishes later — settle fires then, exactly once.
        await _persist_session(db, {
            "loop_id": loop_id, "user_id": u, "project_id": "p1",
            "state": "completed", "phase": "ship", "context": {},
        })
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - LOOP_PRICE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
