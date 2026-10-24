"""tests/test_regression_iter2026_13d_usage_history_ledger.py

Master Prompt v2 PART 4.3 — Settings > Billing > "Usage history": a
separate opt-in page listing ledger rows (date, action name, tokens)
so a user can audit/dispute their own usage. The ONE allowed
exception to "never show per-action numbers in the main app" per the
spec.

Sourced from `real_llm_usage` (the one canonical, already-populated
ledger — see routers/usage.py's existing /me/history, which already
follows the EXACT same "Standing rule (R)" projection: never cost,
model, or provider, at the Mongo level, not just hidden in the
frontend). This new GET /usage/me/ledger groups by (date, surface)
instead of just (date), so each row actually has an action name —
/me/history stays untouched (still used elsewhere for the day-only
chart).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    from cto_services import db as _dbmod
    _dbmod.set_db(database)
    yield database
    _dbmod.set_db(None)
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.real_llm_usage.delete_many({"user_id": {"$regex": "^wallet_test_"}})


async def _seed_usage(db, user_id, *, surface, tokens, cost_usd=1.23, provider="openrouter", model="gpt-x"):
    await db.real_llm_usage.insert_one({
        "user_id": user_id, "ts": time.time(), "status": "ok",
        "surface": surface, "total_tokens": tokens, "cost_usd": cost_usd,
        "provider": provider, "model": model,
    })


class TestUsageLedgerEndpoint:
    async def test_rows_grouped_by_date_and_action(self, db):
        from routers import usage as usage_router
        u = _uid("ledger")
        await _seed_usage(db, u, surface="chat", tokens=10)
        await _seed_usage(db, u, surface="chat", tokens=5)
        await _seed_usage(db, u, surface="fix", tokens=20)

        async def _fake_current_dev(authorization=None):
            return {"user_id": u}
        import unittest.mock
        with unittest.mock.patch.object(usage_router, "current_dev", _fake_current_dev):
            result = await usage_router.my_usage_ledger(authorization="Bearer x")
        rows = result["rows"]
        chat_row = next(r for r in rows if r["action"] == "chat")
        fix_row = next(r for r in rows if r["action"] == "fix")
        assert chat_row["tokens"] == 15
        assert fix_row["tokens"] == 20

    async def test_never_exposes_cost_model_or_provider(self, db):
        from routers import usage as usage_router
        u = _uid("ledgerpriv")
        await _seed_usage(db, u, surface="chat", tokens=10, cost_usd=99.0,
                           provider="super-secret-provider", model="secret-model-v4")

        async def _fake_current_dev(authorization=None):
            return {"user_id": u}
        import unittest.mock
        with unittest.mock.patch.object(usage_router, "current_dev", _fake_current_dev):
            result = await usage_router.my_usage_ledger(authorization="Bearer x")
        blob = str(result)
        assert "99.0" not in blob
        assert "super-secret-provider" not in blob
        assert "secret-model-v4" not in blob
        for row in result["rows"]:
            assert set(row.keys()) == {"date", "action", "tokens"}

    async def test_scoped_to_caller_only(self, db):
        from routers import usage as usage_router
        owner = _uid("ledgerowner")
        other = _uid("ledgerother")
        await _seed_usage(db, owner, surface="chat", tokens=10)
        await _seed_usage(db, other, surface="chat", tokens=999)

        async def _fake_current_dev(authorization=None):
            return {"user_id": owner}
        import unittest.mock
        with unittest.mock.patch.object(usage_router, "current_dev", _fake_current_dev):
            result = await usage_router.my_usage_ledger(authorization="Bearer x")
        total = sum(r["tokens"] for r in result["rows"])
        assert total == 10

    async def test_empty_history_returns_empty_rows_not_error(self, db):
        from routers import usage as usage_router
        u = _uid("ledgerempty")

        async def _fake_current_dev(authorization=None):
            return {"user_id": u}
        import unittest.mock
        with unittest.mock.patch.object(usage_router, "current_dev", _fake_current_dev):
            result = await usage_router.my_usage_ledger(authorization="Bearer x")
        assert result["rows"] == []
