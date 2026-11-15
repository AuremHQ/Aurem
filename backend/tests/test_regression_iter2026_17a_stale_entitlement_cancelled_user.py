"""tests/test_regression_iter2026_17a_stale_entitlement_cancelled_user.py

ITEM 1 (BLOCKER, High) — independent-review fix round.

CURRENT RULE (before this fix), stated from reading
services/fix_entitlement.py: `is_fix_entitled()` trusts
`dev_users.fix_entitlement` directly (a plain bool), modulo the
separate `fix_entitlement_frozen` dispute flag. Nothing cross-checks
it against tier or an active Stripe subscription.

`fix_entitlement` is written:
  - True, UNCONDITIONALLY (no billing_mode() check), by
    grant_fix_entitlement() from the checkout webhook
    (routers/payments.py:777).
  - False, UNCONDITIONALLY, by the refund/dispute revoke paths
    (test_regression_iter2026_11a, no mode check there either).
  - False, but ONLY when `billing_mode() != "off"`, by the
    customer.subscription.deleted/.paused branch
    (routers/payments.py, was ~line 971) — THIS is the one asymmetric
    gate, and the bug: at mode "off" (today), a cancelled/paused
    user's tier flips to "free" and stripe_sub_id to None, but
    fix_entitlement is left stuck at True. If the founder later
    flips TOKEN_BILLING_MODE to "shadow"/"on", that user is treated
    as still entitled to Fix for free.

THE FIX (minimal, does not invent a new global rule):
  1. routers/payments.py — remove the `billing_mode() != "off"` gate
     on this one revoke call, so it matches the grant path (already
     unconditional) and the refund/dispute revoke paths (already
     unconditional). Forward-looking: prevents the bug recurring for
     any cancellation from now on, in any mode.
  2. services/fix_entitlement.py::is_fix_entitled — self-healing for
     rows ALREADY poisoned by the bug before this deploy (a backfill
     migration can't run today per the "no writes to dev_users" rule
     for this fix round). Narrowly detects the EXACT shape the
     cancellation webhook itself produces — `tier == "free"` (exact
     match, not merely absent/None) AND no truthy `stripe_sub_id` —
     and treats a stale `fix_entitlement: True` there as NOT entitled
     unless the user has a positive purchased/plan wallet balance to
     actually spend. Deliberately exact-matches "free" (not falsy/
     absent tier) so it never touches the many existing fixtures
     across this test suite that insert `{"user_id": u}` with no
     tier field at all and expect grant/revoke to work in isolation
     (test_regression_iter2026_10j's test_on_mode_passes_once_granted
     / test_revoke_flips_entitlement_back_to_false, and
     test_regression_iter2026_11c's out-of-order-dispute test) — none
     of those are the cancelled-subscriber shape this bug produces.

Mode flags are untouched by this file: TOKEN_BILLING_MODE stays
whatever the environment already has unless a test explicitly
monkeypatches it for its own duration.
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


def _current_ym() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).strftime("%Y-%m")


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
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})


class TestGateDeniesStaleEntitlementAtModeOn:
    """1(a) — seeded users at mode 'on'."""

    async def test_paid_active_user_is_entitled(self, db, monkeypatch):
        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("paidactive")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "stripe_sub_id": "sub_live",
            "fix_entitlement": True,
        })
        res = await assert_fix_entitled(db, u)
        assert res["entitled"] is True

    async def test_cancelled_user_with_stale_true_is_denied(self, db, monkeypatch):
        """The exact bug: tier free + stripe_sub_id None + fix_entitlement
        stuck True (what the OLD, mode-gated revoke left behind)."""
        from fastapi import HTTPException

        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("cancelledstale")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })
        with pytest.raises(HTTPException) as exc_info:
            await assert_fix_entitled(db, u)
        assert exc_info.value.status_code == 402

    async def test_paused_user_with_stale_true_is_denied(self, db, monkeypatch):
        """customer.subscription.paused hits the SAME branch as .deleted
        in routers/payments.py — identical resulting shape."""
        from fastapi import HTTPException

        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("pausedstale")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })
        with pytest.raises(HTTPException):
            await assert_fix_entitled(db, u)

    async def test_disputed_user_is_denied_via_existing_freeze_flag(self, db, monkeypatch):
        """Disputes already freeze correctly today (freeze_fix_entitlement
        does not touch tier/stripe_sub_id) — confirms this item's fix does
        not need to change that existing, already-correct path."""
        from fastapi import HTTPException

        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("disputedstale")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "stripe_sub_id": "sub_live",
            "fix_entitlement": True, "fix_entitlement_frozen": True,
        })
        with pytest.raises(HTTPException):
            await assert_fix_entitled(db, u)

    async def test_cancelled_user_with_positive_plan_balance_still_entitled(self, db, monkeypatch):
        """The one carve-out this item explicitly asks for: a free-tier,
        no-active-sub user who nonetheless has real plan tokens to spend
        must not be denied just because the subscription ended."""
        from services import wallet
        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("cancelledbalance")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })
        await wallet.grant(
            db, user_id=u, bucket="plan", amount=100,
            idempotency_key=f"leftover:{u}:{_current_ym()}", source="test",
        )
        res = await assert_fix_entitled(db, u)
        assert res["entitled"] is True

    async def test_cancelled_user_with_positive_purchased_balance_still_entitled(self, db, monkeypatch):
        from services import wallet
        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("cancelledpurchased")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })
        await wallet.grant(
            db, user_id=u, bucket="purchased", amount=50,
            idempotency_key=f"topup:{u}", source="test",
        )
        res = await assert_fix_entitled(db, u)
        assert res["entitled"] is True


class TestModeOffUnaffected:
    async def test_mode_off_cancelled_stale_user_still_reports_entitled_true(self, db, monkeypatch):
        """Mode flags stay as they are — at 'off', behavior is
        unconditionally entitled=True regardless of this fix, because
        assert_fix_entitled short-circuits before calling
        is_fix_entitled at all."""
        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "off")
        u = _uid("modeoffcancelled")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })
        res = await assert_fix_entitled(db, u)
        assert res["mode"] == "off"
        assert res["entitled"] is True

    async def test_mode_shadow_cancelled_stale_user_would_block_but_never_raises(self, db, monkeypatch):
        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        u = _uid("modeshadowcancelled")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })
        res = await assert_fix_entitled(db, u)
        assert res["mode"] == "shadow"
        assert res["entitled"] is False


class TestExistingFixturesWithNoTierFieldUnaffected:
    """Regression guard: the narrow tier=="free" check must be an EXACT
    match, never absent/None, so these pre-existing, unrelated fixture
    patterns (grant/revoke round-trip tested in isolation, no tier set
    at all) keep working exactly as before."""

    async def test_bare_fixture_with_no_tier_field_stays_entitled_after_grant(self, db, monkeypatch):
        from services.fix_entitlement import assert_fix_entitled, grant_fix_entitlement
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("baretier")
        await db.dev_users.insert_one({"user_id": u})
        await grant_fix_entitlement(db, u, source="test")
        res = await assert_fix_entitled(db, u)
        assert res["entitled"] is True

    async def test_tier_starter_no_sub_id_not_yet_downgraded_stays_entitled(self, db, monkeypatch):
        """A real pattern from test_regression_iter2026_11c's
        out-of-order-dispute fixture: tier still 'starter' with
        stripe_sub_id None (e.g. between cancellation initiation and
        the tier-downgrade webhook landing) is NOT the cancelled shape
        and must not be penalized."""
        from services.fix_entitlement import assert_fix_entitled, grant_fix_entitlement
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("notyetdowngraded")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter", "stripe_sub_id": None})
        await grant_fix_entitlement(db, u, source="test")
        res = await assert_fix_entitled(db, u)
        assert res["entitled"] is True


class TestWebhookRevokeNoLongerGatedOnMode:
    """1(b) forward-looking fix: the subscription.deleted/.paused branch
    must call revoke_fix_entitlement unconditionally now, matching the
    already-unconditional grant path."""

    async def test_cancellation_webhook_revokes_even_at_mode_off(self, db, monkeypatch):
        from typing import ClassVar

        from routers import payments
        from services.fix_entitlement import is_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "off")
        u = _uid("webhookcancelmodeoff")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "stripe_sub_id": "sub_to_cancel",
            "fix_entitlement": True,
        })
        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_to_cancel"}},
        }
        monkeypatch.setattr(
            payments.stripe.Webhook, "construct_event",
            staticmethod(lambda *a, **kw: event),
        )
        monkeypatch.setattr(payments, "require_db", lambda: db)
        monkeypatch.setattr(payments, "_require_stripe", lambda: None)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

        class _Req:
            headers: ClassVar[dict] = {"stripe-signature": "sig"}
            async def body(self):
                return b"{}"

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

        row = await db.dev_users.find_one({"user_id": u})
        assert row["tier"] == "free"
        assert row["fix_entitlement"] is False, (
            "revoke must now fire regardless of TOKEN_BILLING_MODE — this "
            "was the exact gap that left fix_entitlement stuck True"
        )
        assert await is_fix_entitled(db, u) is False
