"""tests/test_regression_iter2026_10j_fix_entitlement_and_stripe_grants.py

Master Prompt Step C — fix_entitlement + Stripe webhook token grants.

1. services/fix_entitlement.py: off/shadow/on modes, grant/revoke.
2. routers/payments.py's webhook: a plan checkout grants `plan` wallet
   tokens + flips fix_entitlement True; a topup checkout grants
   `purchased` tokens only (never touches tier/entitlement); duplicate
   webhook delivery (same Stripe event id) never double-grants.
3. /payments/status (the frontend's "Confirming payment..." poll
   target) is READ-ONLY for fix_entitlement — a forged/stale
   session_id can never grant it; only the webhook can.
4. Fix endpoints (fix_pipeline.py, cto_projects/tasks.py) 402 in "on"
   when not entitled, and are unaffected ("off"-equivalent today) when
   TOKEN_BILLING_MODE is unset.

Uses a REAL local Mongo for the wallet-touching assertions (same
rationale as test_regression_iter2026_10h) and a lightweight MagicMock
DB (matching tests/test_iter386_error_handling_items.py's established
pattern) for the pure webhook-signature/control-flow tests.
"""
from __future__ import annotations

import os
import uuid
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

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
    yield database
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})


class TestAssertFixEntitled:
    async def test_off_mode_entitled_true_even_with_no_grant(self, db, monkeypatch):
        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "off")
        res = await assert_fix_entitled(db, _uid("notentitled"))
        assert res["mode"] == "off"
        assert res["entitled"] is True

    async def test_shadow_mode_never_raises_but_reports_would_block(self, db, monkeypatch):
        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        res = await assert_fix_entitled(db, _uid("shadownotentitled"))
        assert res["mode"] == "shadow"
        assert res["entitled"] is False

    async def test_on_mode_raises_402_when_not_entitled(self, db, monkeypatch):
        from services.fix_entitlement import assert_fix_entitled
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        with pytest.raises(HTTPException) as exc_info:
            await assert_fix_entitled(db, _uid("onnotentitled"))
        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["code"] == "payment_required"

    async def test_on_mode_passes_once_granted(self, db, monkeypatch):
        from services.fix_entitlement import assert_fix_entitled, grant_fix_entitlement
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("ongranted")
        await db.dev_users.insert_one({"user_id": u})
        await grant_fix_entitlement(db, u, source="test")
        res = await assert_fix_entitled(db, u)
        assert res["entitled"] is True

    async def test_revoke_flips_entitlement_back_to_false(self, db):
        from services.fix_entitlement import (
            grant_fix_entitlement,
            is_fix_entitled,
            revoke_fix_entitlement,
        )
        u = _uid("revoke")
        await db.dev_users.insert_one({"user_id": u})
        await grant_fix_entitlement(db, u, source="test")
        assert await is_fix_entitled(db, u) is True
        await revoke_fix_entitlement(db, u, reason="subscription_cancelled")
        assert await is_fix_entitled(db, u) is False


class TestWebhookGrantsTokensAndEntitlement:
    async def test_plan_checkout_grants_plan_tokens_and_entitlement(self, db, monkeypatch):
        from routers import payments
        u = _uid("webhookplan")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": f"cs_{uuid.uuid4().hex[:10]}",
                "amount_total": 900, "subscription": "sub_1", "customer": "cus_1",
                "metadata": {"user_id": u, "plan": "starter"},
            }},
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

        from services import wallet
        from services.fix_entitlement import is_fix_entitled
        bal = await wallet.get_balance(db, u)
        assert bal["plan"] == 900
        assert await is_fix_entitled(db, u) is True

    async def test_duplicate_webhook_delivery_never_double_grants(self, db, monkeypatch):
        from routers import payments
        u = _uid("webhookdup")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": f"cs_{uuid.uuid4().hex[:10]}",
                "amount_total": 900, "subscription": "sub_1", "customer": "cus_1",
                "metadata": {"user_id": u, "plan": "starter"},
            }},
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

        await payments.stripe_webhook(_Req())
        await payments.stripe_webhook(_Req())  # Stripe redelivers the SAME event

        from services import wallet
        bal = await wallet.get_balance(db, u)
        assert bal["plan"] == 900, "redelivered webhook must not double-grant"

    async def test_topup_checkout_grants_purchased_tokens_only_never_touches_tier(self, db, monkeypatch):
        from routers import payments
        u = _uid("webhooktopup")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": f"cs_{uuid.uuid4().hex[:10]}",
                "amount_total": 1000, "subscription": None, "customer": "cus_1",
                "metadata": {"user_id": u, "kind": "topup", "amount_usd": "10.0"},
            }},
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

        from services import wallet
        from services.fix_entitlement import is_fix_entitled
        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 1000  # $10 * 100 tokens/usd
        assert bal["plan"] == 0
        assert await is_fix_entitled(db, u) is False, "a top-up alone must not unlock Fix"
        user_row = await db.dev_users.find_one({"user_id": u})
        assert user_row["tier"] == "free", "a top-up must never change tier"

    async def test_bad_signature_webhook_rejected(self, monkeypatch):
        import stripe as stripe_sdk

        from routers import payments

        def _raise(*a, **kw):
            raise stripe_sdk.error.SignatureVerificationError("bad sig", "sig_header")
        monkeypatch.setattr(payments.stripe.Webhook, "construct_event", staticmethod(_raise))
        monkeypatch.setattr(payments, "_require_stripe", lambda: None)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

        class _Req:
            headers: ClassVar[dict] = {"stripe-signature": "forged"}
            async def body(self):
                return b"{}"

        with pytest.raises(HTTPException) as exc_info:
            await payments.stripe_webhook(_Req())
        assert exc_info.value.status_code == 400


class TestSubscriptionCancelRevokesFixEntitlement:
    """Step 6 payments-safety review finding: the webhook's
    customer.subscription.deleted/paused branch already downgraded
    `tier` to "free" but never touched `fix_entitlement` — a cancelled
    subscriber kept Fix access forever. Fixed by revoking it in the
    SAME branch, gated on TOKEN_BILLING_MODE != "off"."""

    async def test_subscription_deleted_revokes_entitlement_in_on_mode(self, db, monkeypatch):
        from routers import payments
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("cancelled")
        sub_id = f"sub_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({
            "user_id": u, "tier": "pro", "stripe_sub_id": sub_id,
            "fix_entitlement": True,
        })
        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": sub_id}},
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
            "a cancelled subscription must revoke Fix access, not just "
            "downgrade the display tier"
        )

    async def test_off_mode_downgrades_tier_but_never_touches_fix_entitlement_field(
        self, db, monkeypatch,
    ):
        """Hard rule: in off mode, this new revocation must not touch
        existing users' fields beyond what the webhook already wrote
        before this fix existed."""
        from routers import payments
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        u = _uid("cancelledoff")
        sub_id = f"sub_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({
            "user_id": u, "tier": "pro", "stripe_sub_id": sub_id,
        })
        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": sub_id}},
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

        await payments.stripe_webhook(_Req())

        row = await db.dev_users.find_one({"user_id": u})
        assert row["tier"] == "free"
        assert "fix_entitlement" not in row, (
            "off mode must not write the fix_entitlement field at all"
        )


class TestPaymentStatusNeverGrantsEntitlement:
    async def test_forged_session_poll_cannot_grant_fix_entitlement(self, db, monkeypatch):
        """payment_status is a GET endpoint the frontend polls after
        the Stripe redirect. It must only ever READ fix_entitlement,
        never set it — even for a session Stripe itself reports as
        paid. Only the webhook may grant."""
        from routers import payments
        u = _uid("statuspoll")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        session_id = f"cs_{uuid.uuid4().hex[:10]}"
        await db.cto_payments.insert_one({
            "session_id": session_id, "user_id": u, "plan": "starter",
            "status": "initiated", "payment_status": "pending",
        })

        fake_session = MagicMock()
        fake_session.payment_status = "paid"
        fake_session.status = "complete"
        fake_session.subscription = "sub_1"

        async def _fake_current_dev(authorization=None):
            return {"user_id": u, "email": "x@example.com"}

        async def _fake_check_permission(user, *a, **kw):
            return {"effective_account_id": u, "is_owner": True}

        monkeypatch.setattr(payments, "current_dev", _fake_current_dev)
        monkeypatch.setattr(payments, "check_permission", _fake_check_permission)
        monkeypatch.setattr(payments, "require_db", lambda: db)
        monkeypatch.setattr(payments, "_require_stripe", lambda: None)
        monkeypatch.setattr(payments, "_stripe_call", AsyncMock(return_value=fake_session))

        result = await payments.payment_status(session_id, authorization="Bearer x")
        assert result["payment_status"] == "paid"
        assert result["fix_entitled"] is False, (
            "status-poll must report the REAL (false) entitlement — "
            "it must never grant it itself, even though Stripe says paid"
        )

        from services.fix_entitlement import is_fix_entitled
        assert await is_fix_entitled(db, u) is False, (
            "polling /payments/status must NEVER set fix_entitlement — "
            "only the signature-verified webhook may"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
