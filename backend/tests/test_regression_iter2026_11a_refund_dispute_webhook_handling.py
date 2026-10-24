"""tests/test_regression_iter2026_11a_refund_dispute_webhook_handling.py

Master Prompt v2 Part 1.3 — routers/payments.py::stripe_webhook's new
`charge.refunded` / `charge.dispute.created` branches.

Founder-approved defaults under test:
  - Refund of a TOP-UP payment claws back unspent `purchased` tokens
    only, capped at the current balance (never below zero); any
    shortfall (already-spent tokens) is flagged, not clawed back from
    elsewhere.
  - FULL refund of a SUBSCRIPTION charge claws back unspent `plan`
    tokens and revokes fix_entitlement ONLY if no active subscription
    remains (dev_users.stripe_sub_id is falsy).
  - PARTIAL refund of a subscription charge is log-only — no token or
    entitlement change.
  - charge.dispute.created freezes fix_entitlement (non-destructively)
    until a human resolves it, and flags it for admin review.
  - A payment whose kind can't be identified from metadata (or a
    dispute whose underlying charge has no metadata) is a no-op —
    fail safe.
  - Everything is idempotent per Stripe event id.
"""
from __future__ import annotations

import os
import uuid
from typing import ClassVar

import pytest

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


class _Req:
    headers: ClassVar[dict] = {"stripe-signature": "sig"}

    async def body(self):
        return b"{}"


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
        await db.billing_flags.delete_many({"user_id": {"$regex": "^wallet_test_"}})


def _wire_webhook(payments, db, monkeypatch, event: dict):
    monkeypatch.setattr(
        payments.stripe.Webhook, "construct_event",
        staticmethod(lambda *a, **kw: event),
    )
    monkeypatch.setattr(payments, "require_db", lambda: db)
    monkeypatch.setattr(payments, "_require_stripe", lambda: None)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")


def _refund_event(*, charge_id=None, metadata=None, amount=1000, amount_refunded=1000):
    return {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "type": "charge.refunded",
        "data": {"object": {
            "id": charge_id or f"ch_{uuid.uuid4().hex[:10]}",
            "amount": amount,
            "amount_refunded": amount_refunded,
            "metadata": metadata or {},
        }},
    }


def _dispute_event(*, dispute_id=None, charge_id=None, reason="fraudulent"):
    return {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "type": "charge.dispute.created",
        "data": {"object": {
            "id": dispute_id or f"dp_{uuid.uuid4().hex[:10]}",
            "charge": charge_id,
            "reason": reason,
        }},
    }


def _dispute_closed_event(*, dispute_id=None, charge_id=None, status="won", amount=1000):
    return {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "type": "charge.dispute.closed",
        "data": {"object": {
            "id": dispute_id or f"dp_{uuid.uuid4().hex[:10]}",
            "charge": charge_id,
            "status": status,
            "amount": amount,
        }},
    }


def _current_ym() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).strftime("%Y-%m")


def _fake_charge_metadata(payments, monkeypatch, metadata: dict):
    class _FakeCharge:
        pass
    _FakeCharge.metadata = metadata
    monkeypatch.setattr(
        payments, "_stripe_call",
        lambda fn, *a, **kw: _async_return(_FakeCharge()),
    )


class TestTopupRefund:
    async def test_full_refund_claws_back_exact_purchased_tokens(self, db, monkeypatch):
        from routers import payments
        from services import wallet
        u = _uid("topuprefund")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        await wallet.grant(
            db, user_id=u, bucket="purchased", amount=1000,
            idempotency_key=f"grant_{u}", source="test",
        )
        event = _refund_event(
            metadata={"user_id": u, "kind": "topup", "amount_usd": "10.0", "batch_id": f"grant_{u}"},
            amount=1000, amount_refunded=1000,
        )
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 0

        row = await db.dev_users.find_one({"user_id": u})
        assert row["tier"] == "free"
        assert "fix_entitlement" not in row, "a top-up refund must never touch tier/entitlement"

    async def test_partial_refund_claws_back_proportional_tokens(self, db, monkeypatch):
        from routers import payments
        from services import wallet
        u = _uid("topuppartial")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        await wallet.grant(
            db, user_id=u, bucket="purchased", amount=1000,
            idempotency_key=f"grant_{u}", source="test",
        )
        # half the $10 top-up refunded -> half the tokens clawed back
        event = _refund_event(
            metadata={"user_id": u, "kind": "topup", "amount_usd": "10.0", "batch_id": f"grant_{u}"},
            amount=1000, amount_refunded=500,
        )
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())

        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 500

    async def test_already_spent_tokens_flag_shortfall_never_go_negative(self, db, monkeypatch):
        from routers import payments
        from services import wallet
        u = _uid("topupshortfall")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        await wallet.grant(
            db, user_id=u, bucket="purchased", amount=200,
            idempotency_key=f"grant_{u}", source="test",
        )
        # refund corresponds to 1000 tokens but only 200 remain unspent
        event = _refund_event(
            metadata={"user_id": u, "kind": "topup", "amount_usd": "10.0", "batch_id": f"grant_{u}"},
            amount=1000, amount_refunded=1000,
        )
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())

        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 0, "must never go negative"

        flag = await db.billing_flags.find_one({"user_id": u, "flag_type": "refund_shortfall"})
        assert flag is not None
        assert flag["detail"]["shortfall_tokens"] == 800

    async def test_duplicate_event_never_double_claws_back(self, db, monkeypatch):
        from routers import payments
        from services import wallet
        u = _uid("topupdup")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        await wallet.grant(
            db, user_id=u, bucket="purchased", amount=1000,
            idempotency_key=f"grant_{u}", source="test",
        )
        event = _refund_event(
            metadata={"user_id": u, "kind": "topup", "amount_usd": "10.0", "batch_id": f"grant_{u}"},
            amount=1000, amount_refunded=500,
        )
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())
        await payments.stripe_webhook(_Req())  # Stripe redelivers the SAME event

        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 500, "redelivered refund event must not double-claw-back"


class TestSubscriptionRefund:
    async def test_full_refund_with_no_active_sub_revokes_entitlement(self, db, monkeypatch):
        from routers import payments
        from services import wallet
        from services.fix_entitlement import is_fix_entitled
        u = _uid("subrefundfull")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "fix_entitlement": True,
            "stripe_sub_id": None,  # already cancelled — no active sub remains
        })
        await wallet.grant(
            db, user_id=u, bucket="plan", amount=900,
            idempotency_key=f"monthly_grant:{u}:{_current_ym()}", source="test",
        )
        event = _refund_event(
            metadata={"user_id": u, "plan": "starter"},
            amount=900, amount_refunded=900,
        )
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())

        bal = await wallet.get_balance(db, u)
        assert bal["plan"] == 0
        assert await is_fix_entitled(db, u) is False

    async def test_full_refund_with_active_sub_keeps_entitlement(self, db, monkeypatch):
        from routers import payments
        from services import wallet
        from services.fix_entitlement import is_fix_entitled
        u = _uid("subrefundactive")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "fix_entitlement": True,
            "stripe_sub_id": "sub_still_active",
        })
        await wallet.grant(
            db, user_id=u, bucket="plan", amount=900,
            idempotency_key=f"monthly_grant:{u}:{_current_ym()}", source="test",
        )
        event = _refund_event(
            metadata={"user_id": u, "plan": "starter"},
            amount=900, amount_refunded=900,
        )
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())

        bal = await wallet.get_balance(db, u)
        assert bal["plan"] == 0, "tokens are still clawed back"
        assert await is_fix_entitled(db, u) is True, (
            "must not revoke entitlement while another active subscription remains"
        )

    async def test_partial_refund_logs_only_no_token_or_entitlement_change(self, db, monkeypatch):
        from routers import payments
        from services import wallet
        from services.fix_entitlement import is_fix_entitled
        u = _uid("subrefundpartial")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "fix_entitlement": True,
            "stripe_sub_id": None,
        })
        await wallet.grant(
            db, user_id=u, bucket="plan", amount=900,
            idempotency_key=f"grant_{u}", source="test",
        )
        event = _refund_event(
            metadata={"user_id": u, "plan": "starter"},
            amount=900, amount_refunded=300,
        )
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())

        bal = await wallet.get_balance(db, u)
        assert bal["plan"] == 900, "a partial subscription refund must not touch tokens"
        assert await is_fix_entitled(db, u) is True, (
            "a partial subscription refund must not touch entitlement"
        )
        flag = await db.billing_flags.find_one({"user_id": u, "flag_type": "partial_sub_refund"})
        assert flag is not None


class TestUnknownPayment:
    async def test_no_user_id_in_metadata_is_a_safe_noop(self, db, monkeypatch):
        from routers import payments
        event = _refund_event(metadata={}, amount=1000, amount_refunded=1000)
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}, "an unidentifiable refund must never raise or 500"

    async def test_metadata_with_user_but_no_kind_or_plan_is_a_safe_noop(self, db, monkeypatch):
        from routers import payments
        u = _uid("unknownkind")
        await db.dev_users.insert_one({"user_id": u, "tier": "free"})
        event = _refund_event(
            metadata={"user_id": u}, amount=1000, amount_refunded=1000,
        )
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

        from services import wallet
        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 0 and bal["plan"] == 0


class TestDisputeCreated:
    async def test_dispute_freezes_entitlement_and_flags_it(self, db, monkeypatch):
        from routers import payments
        from services.fix_entitlement import grant_fix_entitlement, is_fix_entitled
        u = _uid("dispute")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        await grant_fix_entitlement(db, u, source="test")
        assert await is_fix_entitled(db, u) is True

        class _FakeCharge:
            metadata: ClassVar[dict] = {"user_id": u, "plan": "starter"}

        monkeypatch.setattr(
            payments, "_stripe_call",
            lambda fn, *a, **kw: _async_return(_FakeCharge()),
        )
        event = _dispute_event(charge_id=charge_id)
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

        assert await is_fix_entitled(db, u) is False, "a dispute must freeze Fix access"
        row = await db.dev_users.find_one({"user_id": u})
        assert row["fix_entitlement"] is True, (
            "freezing must NOT destructively clear the underlying "
            "fix_entitlement value — only overlay it"
        )
        flag = await db.billing_flags.find_one({"user_id": u, "flag_type": "dispute_freeze"})
        assert flag is not None
        assert flag["detail"]["dispute_id"] == event["data"]["object"]["id"]

    async def test_dispute_with_no_charge_metadata_is_a_safe_noop(self, db, monkeypatch):
        from routers import payments

        class _FakeCharge:
            metadata: ClassVar[dict] = {}

        monkeypatch.setattr(
            payments, "_stripe_call",
            lambda fn, *a, **kw: _async_return(_FakeCharge()),
        )
        event = _dispute_event(charge_id="ch_nouser")
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

    async def test_dispute_charge_retrieve_failure_is_a_safe_noop(self, db, monkeypatch):
        from routers import payments

        async def _raise(fn, *a, **kw):
            raise RuntimeError("stripe api down")
        monkeypatch.setattr(payments, "_stripe_call", _raise)
        event = _dispute_event(charge_id="ch_apidown")
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}, "a charge-retrieve failure must not crash the webhook"

    async def test_duplicate_dispute_event_never_double_flags(self, db, monkeypatch):
        from routers import payments
        from services.fix_entitlement import grant_fix_entitlement
        u = _uid("disputedup")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        await grant_fix_entitlement(db, u, source="test")

        class _FakeCharge:
            metadata: ClassVar[dict] = {"user_id": u, "plan": "starter"}

        monkeypatch.setattr(
            payments, "_stripe_call",
            lambda fn, *a, **kw: _async_return(_FakeCharge()),
        )
        event = _dispute_event(charge_id=charge_id)
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())
        await payments.stripe_webhook(_Req())

        flags = db.billing_flags.find({"user_id": u, "flag_type": "dispute_freeze"})
        count = 0
        async for _ in flags:
            count += 1
        assert count == 1, "a redelivered dispute event must not double-flag"


class TestDisputeClosed:
    async def test_won_unfreezes_and_restores_exactly_what_user_had(self, db, monkeypatch):
        from routers import payments
        from services.fix_entitlement import (
            freeze_fix_entitlement,
            grant_fix_entitlement,
            is_fix_entitled,
        )
        u = _uid("disputewon")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        await grant_fix_entitlement(db, u, source="test")
        await freeze_fix_entitlement(db, u, reason="stripe_dispute:dp_x")
        assert await is_fix_entitled(db, u) is False

        _fake_charge_metadata(payments, monkeypatch, {"user_id": u, "plan": "starter"})
        event = _dispute_closed_event(charge_id=charge_id, status="won")
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

        assert await is_fix_entitled(db, u) is True, (
            "a won dispute must restore exactly what the user had before"
        )
        flag = await db.billing_flags.find_one({"user_id": u, "flag_type": "dispute_won"})
        assert flag is not None

    async def test_lost_revokes_and_claws_back_when_no_active_sub(self, db, monkeypatch):
        from routers import payments
        from services import wallet
        from services.fix_entitlement import (
            freeze_fix_entitlement,
            grant_fix_entitlement,
            is_fix_entitled,
        )
        u = _uid("disputelost")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "stripe_sub_id": None,
        })
        await grant_fix_entitlement(db, u, source="test")
        await freeze_fix_entitlement(db, u, reason="stripe_dispute:dp_x")
        await wallet.grant(
            db, user_id=u, bucket="plan", amount=900,
            idempotency_key=f"monthly_grant:{u}:{_current_ym()}", source="test",
        )

        _fake_charge_metadata(payments, monkeypatch, {"user_id": u, "plan": "starter"})
        event = _dispute_closed_event(charge_id=charge_id, status="lost", amount=900)
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

        assert await is_fix_entitled(db, u) is False, (
            "a lost dispute must leave the user unentitled"
        )
        row = await db.dev_users.find_one({"user_id": u})
        assert row["fix_entitlement"] is False, (
            "a lost dispute (no active sub remaining) must destructively "
            "revoke fix_entitlement, not just leave the freeze in place"
        )
        bal = await wallet.get_balance(db, u)
        assert bal["plan"] == 0, "unspent plan tokens for the disputed amount must be clawed back"
        flag = await db.billing_flags.find_one({"user_id": u, "flag_type": "dispute_lost"})
        assert flag is not None

    async def test_lost_keeps_entitlement_revoked_capped_clawback_when_mostly_spent(
        self, db, monkeypatch,
    ):
        from routers import payments
        from services import wallet
        u = _uid("disputelostshort")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
        })
        await wallet.grant(
            db, user_id=u, bucket="purchased", amount=100,
            idempotency_key=f"grant_{u}", source="test",
        )

        _fake_charge_metadata(
            payments, monkeypatch,
            {"user_id": u, "kind": "topup", "amount_usd": "10.0", "batch_id": f"grant_{u}"},
        )
        event = _dispute_closed_event(charge_id=charge_id, status="lost", amount=1000)
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())

        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 0, "clawback must never go negative"
        flag = await db.billing_flags.find_one({"user_id": u, "flag_type": "dispute_lost"})
        assert flag["detail"]["shortfall_tokens"] == 900

    async def test_lost_with_active_sub_does_not_revoke(self, db, monkeypatch):
        from routers import payments
        from services.fix_entitlement import grant_fix_entitlement, is_fix_entitled
        u = _uid("disputelostactive")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "stripe_sub_id": "sub_still_active",
        })
        await grant_fix_entitlement(db, u, source="test")

        _fake_charge_metadata(payments, monkeypatch, {"user_id": u, "plan": "starter"})
        event = _dispute_closed_event(charge_id=charge_id, status="lost", amount=900)
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())

        assert await is_fix_entitled(db, u) is True, (
            "must not revoke while another active subscription remains"
        )

    async def test_other_status_is_log_only_no_state_change(self, db, monkeypatch):
        from routers import payments
        from services.fix_entitlement import (
            freeze_fix_entitlement,
            grant_fix_entitlement,
            is_fix_entitled,
        )
        u = _uid("disputewarning")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        await grant_fix_entitlement(db, u, source="test")
        await freeze_fix_entitlement(db, u, reason="stripe_dispute:dp_x")

        _fake_charge_metadata(payments, monkeypatch, {"user_id": u, "plan": "starter"})
        event = _dispute_closed_event(charge_id=charge_id, status="warning_closed")
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

        assert await is_fix_entitled(db, u) is False, "an unhandled status must not unfreeze"
        row = await db.dev_users.find_one({"user_id": u})
        assert row["fix_entitlement"] is True, "an unhandled status must not revoke"
        assert await db.billing_flags.find_one(
            {"user_id": u, "flag_type": {"$in": ["dispute_won", "dispute_lost"]}},
        ) is None

    async def test_unknown_payment_no_metadata_is_a_safe_noop(self, db, monkeypatch):
        from routers import payments
        _fake_charge_metadata(payments, monkeypatch, {})
        event = _dispute_closed_event(charge_id="ch_nouser", status="won")
        _wire_webhook(payments, db, monkeypatch, event)

        resp = await payments.stripe_webhook(_Req())
        assert resp == {"ok": True}

    async def test_duplicate_won_event_never_double_unfreezes_or_flags(self, db, monkeypatch):
        from routers import payments
        from services.fix_entitlement import (
            freeze_fix_entitlement,
            grant_fix_entitlement,
        )
        u = _uid("disputewondup")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        await grant_fix_entitlement(db, u, source="test")
        await freeze_fix_entitlement(db, u, reason="stripe_dispute:dp_x")

        _fake_charge_metadata(payments, monkeypatch, {"user_id": u, "plan": "starter"})
        event = _dispute_closed_event(charge_id=charge_id, status="won")
        _wire_webhook(payments, db, monkeypatch, event)

        await payments.stripe_webhook(_Req())
        await payments.stripe_webhook(_Req())

        flags = db.billing_flags.find({"user_id": u, "flag_type": "dispute_won"})
        count = 0
        async for _ in flags:
            count += 1
        assert count == 1, "a redelivered dispute.closed event must not double-flag"


async def _async_return(value):
    return value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
