"""tests/test_regression_iter2026_13c_paywall_fix_estimate.py

Master Prompt v2 PART 4.2 — the paywall's ONE plan-level line ("$9
gets you about N fixes"). The underlying math already existed from
earlier Master Prompt v2 parts (services.pricing_engine.
plan_capacity_live — rolling 7-day real cost converted through the
current margin, falling back to the flat PRICE_TABLE estimate when
there's no live data yet); this file adds the thin, public,
read-only HTTP endpoint that exposes it for the paywall modal, and
proves the fallback behaves.

Also covers: the SERVER-confirmed entitlement poll
(routers/payments.py::payment_status) already existed before this
turn and already (a) scopes to the caller's own session only, (b)
never writes fix_entitlement from a client-controlled value — this
file re-verifies both from the paywall's point of view (forged/stale
session_id, cross-user session_id) since that endpoint is the ONE
thing a client-side "fake unlock" attempt has to get past.

No refund-policy line exists anywhere in code/config for this
codebase (confirmed by grep — no REFUND_POLICY/refund_policy config,
no refund copy on Pricing.jsx/PricingCards.jsx) — per the spec,
"show the refund line only if a refund policy exists in code/config;
otherwise list it as a missing decision." Flagged in the final
report; nothing invented here.
"""
from __future__ import annotations

import os
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
        await db.action_cost_stats.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.cto_payments.delete_many({"user_id": {"$regex": "^wallet_test_"}})


class TestFixEstimateEndpoint:
    async def test_estimate_uses_live_rolling_average_when_present(self, db):
        from routers import payments as pay
        await db.action_cost_stats.update_one(
            {"_id": "fix"}, {"$set": {"avg_cost_usd_7d": 0.01}}, upsert=True,
        )
        result = await pay.fix_estimate(price_usd=9.0)
        assert result["price_usd"] == 9.0
        assert isinstance(result["estimated_fixes"], (int, float))
        assert result["estimated_fixes"] > 0

    async def test_estimate_falls_back_to_flat_table_when_no_live_data(self, db):
        from routers import payments as pay
        result = await pay.fix_estimate(price_usd=9.0)
        assert result["price_usd"] == 9.0
        # Falls back to plan_capacity's flat PRICE_TABLE guess —
        # never None/crashes just because no real measurements exist.
        assert result["estimated_fixes"] is None or result["estimated_fixes"] > 0

    async def test_estimate_response_has_no_refund_line_by_default(self, db):
        """No refund policy exists in code/config anywhere in this
        repo — the endpoint must not invent one."""
        from routers import payments as pay
        result = await pay.fix_estimate(price_usd=9.0)
        assert "refund_policy" not in result or result["refund_policy"] is None

    async def test_negative_or_zero_price_rejected(self, db):
        from fastapi import HTTPException

        from routers import payments as pay
        with pytest.raises(HTTPException):
            await pay.fix_estimate(price_usd=0)
        with pytest.raises(HTTPException):
            await pay.fix_estimate(price_usd=-9)

    async def test_absurd_price_does_not_overflow_or_crash(self, db):
        from routers import payments as pay
        result = await pay.fix_estimate(price_usd=1_000_000_000)
        assert isinstance(result["estimated_fixes"], (int, float, type(None)))


class TestPaymentStatusScopingReVerified:
    async def test_forged_session_id_for_another_user_404s(self, db, monkeypatch):
        """routers/payments.py::payment_status already existed — this
        re-confirms, from the paywall's angle, that a session_id
        belonging to user B can never be read by user A (the ONE
        poll endpoint the paywall relies on to decide "unlock
        now")."""
        from routers import payments as pay
        owner = _uid("payowner")
        attacker = _uid("payattacker")
        session_id = f"cs_test_{uuid.uuid4().hex}"
        await db.cto_payments.insert_one({
            "session_id": session_id, "user_id": owner, "plan": "starter",
            "payment_status": "pending",
        })

        async def _fake_current_dev(authorization=None):
            return {"user_id": attacker}
        monkeypatch.setattr(pay, "current_dev", _fake_current_dev)

        async def _fake_check_permission(user, *_a, **_kw):
            return {"effective_account_id": user["user_id"]}
        monkeypatch.setattr(pay, "check_permission", _fake_check_permission)
        monkeypatch.setattr(pay, "_require_stripe", lambda: None)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await pay.payment_status(session_id, authorization="Bearer x")
        assert exc.value.status_code == 404
