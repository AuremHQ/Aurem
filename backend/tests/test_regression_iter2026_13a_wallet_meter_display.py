"""tests/test_regression_iter2026_13a_wallet_meter_display.py

Master Prompt v2 PART 4.3 — the user-facing token METER. Per the
spec's strict display rules:
  - The user sees ONLY the remaining balance + a bar + the refill
    date, and ONLY after they have a paid plan.
  - NEVER a per-action/per-tool-call number anywhere.
  - Thresholds by % of the month's starting balance: 20% yellow,
    5% red, 0 = stop. Founder/unlimited accounts never see any of
    this (same bypass TokenBell.jsx already had).
  - TOKEN_BILLING_MODE stays "off" for this delivery — the meter
    must stay invisible in that mode (nothing changes for real users
    yet), while remaining fully buildable/testable with the mode
    flipped to "shadow"/"on" (same pattern every other Master Prompt
    v2 part already uses for billing_mode()-gated code).

services/wallet_display.py::get_user_meter(db, user_id) is the ONE
function that computes this (new this turn). routers/usage.py's new
GET /usage/me/meter is the thin, caller-scoped HTTP wrapper around it.

TDD: written and run RED before get_user_meter/the route exist.
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
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})


async def _make_user(db, *, tier="starter", is_unlimited=False):
    u = _uid("meter")
    await db.dev_users.update_one(
        {"user_id": u},
        {"$set": {"user_id": u, "tier": tier, "is_unlimited": is_unlimited,
                   "email": f"{u}@example.com"}},
        upsert=True,
    )
    return u


async def _set_wallet(db, user_id, *, spendable_bucket="plan", amount, base, refill_at=None):
    from services.wallet import grant
    if amount > 0:
        await grant(db, user_id=user_id, bucket=spendable_bucket, amount=amount,
                    idempotency_key=f"meter_grant_{user_id}", source="test")
    await db.wallets.update_one(
        {"_id": user_id},
        {"$set": {"month_baseline": {"ym": "2026-10", "amount": base},
                   "plan_refill_at": refill_at or time.time() + 86400 * 5}},
        upsert=True,
    )


class TestMeterHiddenCases:
    async def test_hidden_when_billing_mode_off(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "off")
        u = await _make_user(db, tier="starter")
        await _set_wallet(db, u, amount=800, base=1000)
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["visible"] is False

    async def test_hidden_for_free_tier_even_with_mode_on(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="free")
        await _set_wallet(db, u, amount=800, base=1000)
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["visible"] is False

    async def test_hidden_for_unlimited_founder_even_with_mode_on(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="founder", is_unlimited=True)
        await _set_wallet(db, u, amount=800, base=1000)
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["visible"] is False

    async def test_visible_for_paid_tier_with_mode_shadow(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "shadow")
        u = await _make_user(db, tier="starter")
        await _set_wallet(db, u, amount=800, base=1000)
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["visible"] is True


class TestMeterNeverLeaksPerActionNumbers:
    async def test_meter_response_has_no_per_action_or_cost_fields(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="pro")
        await _set_wallet(db, u, amount=800, base=1000)
        meter = await wallet_display.get_user_meter(db, u)
        forbidden = {"cost_usd", "action", "tool", "per_action", "margin",
                     "actual_cost_usd", "held_amount", "price"}
        assert not (forbidden & set(meter.keys())), meter.keys()
        assert set(meter.keys()) == {
            "visible", "balance", "base", "pct_remaining",
            "refill_date", "threshold",
        }


class TestThresholds:
    async def test_threshold_ok_above_20_pct(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="starter")
        await _set_wallet(db, u, amount=300, base=1000)  # 30%
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["threshold"] == "ok"
        assert meter["pct_remaining"] == 30.0

    async def test_threshold_yellow_at_20_pct(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="starter")
        await _set_wallet(db, u, amount=200, base=1000)  # exactly 20%
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["threshold"] == "yellow"

    async def test_threshold_red_at_5_pct(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="starter")
        await _set_wallet(db, u, amount=50, base=1000)  # exactly 5%
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["threshold"] == "red"

    async def test_threshold_stop_at_zero(self, db, monkeypatch):
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="starter")
        await _set_wallet(db, u, amount=0, base=1000)
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["threshold"] == "stop"

    async def test_no_base_yet_never_crashes(self, db, monkeypatch):
        """A brand-new wallet with no month_baseline set yet (grant()
        never ran) must degrade gracefully, not raise or divide by
        zero."""
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="starter")
        meter = await wallet_display.get_user_meter(db, u)
        assert meter["visible"] in (True, False)
        assert meter["pct_remaining"] is None or isinstance(meter["pct_remaining"], float)


class TestMeterEndpointScoping:
    async def test_endpoint_returns_only_the_callers_own_meter(self, db, monkeypatch):
        from routers import usage as usage_router
        from services import wallet_display
        monkeypatch.setattr(wallet_display, "billing_mode", lambda: "on")
        u = await _make_user(db, tier="starter")
        await _set_wallet(db, u, amount=400, base=1000)

        async def _fake_current_dev(authorization=None):
            return {"user_id": u, "tier": "starter", "email": f"{u}@example.com"}
        monkeypatch.setattr(usage_router, "current_dev", _fake_current_dev)
        result = await usage_router.my_usage_meter(authorization="Bearer x")
        assert result["visible"] is True
        assert result["balance"] == 400
