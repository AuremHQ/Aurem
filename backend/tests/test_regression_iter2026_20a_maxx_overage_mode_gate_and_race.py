"""tests/test_regression_iter2026_20a_maxx_overage_mode_gate_and_race.py

ITEM 4 (Medium) — independent-review fix round.

4(a)/4(b): services/billing_cron.py::bill_maxx_overages had NO
TOKEN_BILLING_MODE check at all — it invoiced $0.50/overage task via
Stripe unconditionally. [DEFAULT-PENDING-FOUNDER, not yet final]:
under shadow/on, overage invoicing is OFF (the token-billing model
replaces per-action overage charges); the counter
(services.usage.incr_maxx_usage) keeps counting for analytics only.
At mode "off", behavior is byte-identical to before — amounts and
descriptions are untouched either way.

4(c): a READ-ONLY report of current-month overage_count > 0 users and
the total USD at $0.50/task, with no invoicing or reset.

4(d): services/usage.py::incr_maxx_usage read `capped` state via a
SEPARATE get_maxx_usage() call, then incremented via its own
find_one_and_update — a classic TOCTOU: concurrent calls can all read
"not yet capped" and all skip the overage bump, undercounting
overage_count under concurrency. Fixed with a single atomic pipeline
update (same style as services/wallet.py::_atomic_bucket_spend) that
computes the post-increment count and the overage decision in one
document update.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import MagicMock

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
        await db.cto_maxx_usage.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.billing_cron_runs.delete_many({})


class TestModeGateOnBillMaxxOverages:
    async def test_mode_off_invoices_as_before(self, db, monkeypatch):
        from services import billing_cron
        monkeypatch.setenv("TOKEN_BILLING_MODE", "off")
        u = _uid("modeoff")
        bucket = _current_ym()
        await db.dev_users.insert_one({
            "user_id": u, "tier": "pro", "stripe_customer_id": "cus_x",
        })
        await db.cto_maxx_usage.insert_one({"user_id": u, "month": bucket, "overage_count": 3, "count": 103})

        fake_stripe = MagicMock()
        fake_invoice = MagicMock()
        fake_invoice.id = "in_fake"
        fake_stripe.InvoiceItem.create.return_value = None
        fake_stripe.Invoice.create.return_value = fake_invoice
        fake_stripe.Invoice.finalize_invoice.return_value = None
        monkeypatch.setattr(billing_cron, "_stripe_client", lambda: fake_stripe)

        result = await billing_cron.bill_maxx_overages(db)
        assert result["billed"] == 1
        assert fake_stripe.InvoiceItem.create.called
        assert fake_stripe.Invoice.create.called
        row = await db.cto_maxx_usage.find_one({"user_id": u, "month": bucket})
        assert row["overage_count"] == 0, "mode off resets the counter after billing, as before"

    async def test_mode_shadow_makes_zero_stripe_calls_counter_untouched(self, db, monkeypatch):
        from services import billing_cron
        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        u = _uid("modeshadow")
        bucket = _current_ym()
        await db.dev_users.insert_one({"user_id": u, "tier": "pro", "stripe_customer_id": "cus_x"})
        await db.cto_maxx_usage.insert_one({"user_id": u, "month": bucket, "overage_count": 3, "count": 103})

        fake_stripe = MagicMock()
        monkeypatch.setattr(billing_cron, "_stripe_client", lambda: fake_stripe)

        result = await billing_cron.bill_maxx_overages(db)
        assert not fake_stripe.InvoiceItem.create.called
        assert not fake_stripe.Invoice.create.called
        assert not fake_stripe.Invoice.finalize_invoice.called
        row = await db.cto_maxx_usage.find_one({"user_id": u, "month": bucket})
        assert row["overage_count"] == 3, "shadow mode must not reset the analytics counter"
        assert result["mode"] == "shadow"

    async def test_mode_on_makes_zero_stripe_calls_counter_untouched(self, db, monkeypatch):
        from services import billing_cron
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("modeon")
        bucket = _current_ym()
        await db.dev_users.insert_one({"user_id": u, "tier": "pro", "stripe_customer_id": "cus_x"})
        await db.cto_maxx_usage.insert_one({"user_id": u, "month": bucket, "overage_count": 3, "count": 103})

        fake_stripe = MagicMock()
        monkeypatch.setattr(billing_cron, "_stripe_client", lambda: fake_stripe)

        result = await billing_cron.bill_maxx_overages(db)
        assert not fake_stripe.InvoiceItem.create.called
        row = await db.cto_maxx_usage.find_one({"user_id": u, "month": bucket})
        assert row["overage_count"] == 3
        assert result["mode"] == "on"

    async def test_idempotent_rerun_at_mode_off_never_double_bills(self, db, monkeypatch):
        from services import billing_cron
        monkeypatch.setenv("TOKEN_BILLING_MODE", "off")
        u = _uid("idempotent")
        bucket = _current_ym()
        await db.dev_users.insert_one({"user_id": u, "tier": "pro", "stripe_customer_id": "cus_x"})
        await db.cto_maxx_usage.insert_one({"user_id": u, "month": bucket, "overage_count": 2, "count": 102})

        fake_stripe = MagicMock()
        fake_invoice = MagicMock()
        fake_invoice.id = "in_fake"
        fake_stripe.Invoice.create.return_value = fake_invoice
        monkeypatch.setattr(billing_cron, "_stripe_client", lambda: fake_stripe)

        r1 = await billing_cron.bill_maxx_overages(db)
        assert r1["billed"] == 1
        r2 = await billing_cron.bill_maxx_overages(db)
        assert r2["billed"] == 0, "overage_count is already 0 — nothing left to bill"
        assert fake_stripe.Invoice.create.call_count == 1


class TestReadOnlyOverageReport:
    async def test_report_lists_users_and_total_usd_without_invoicing(self, db, monkeypatch):
        from services import billing_cron
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        bucket = _current_ym()
        u1 = _uid("reportuser1")
        u2 = _uid("reportuser2")
        await db.dev_users.insert_many([
            {"user_id": u1, "tier": "pro", "stripe_customer_id": "cus_1"},
            {"user_id": u2, "tier": "pro", "stripe_customer_id": "cus_2"},
        ])
        await db.cto_maxx_usage.insert_many([
            {"user_id": u1, "month": bucket, "overage_count": 4, "count": 104},
            {"user_id": u2, "month": bucket, "overage_count": 2, "count": 102},
        ])
        fake_stripe = MagicMock()
        monkeypatch.setattr(billing_cron, "_stripe_client", lambda: fake_stripe)

        report = await billing_cron.maxx_overage_preflight_report(db)
        ids = {r["user_id"] for r in report["rows"]}
        assert {u1, u2} <= ids
        row1 = next(r for r in report["rows"] if r["user_id"] == u1)
        assert row1["overage_count"] == 4
        assert row1["amount_usd"] == 2.0
        assert report["total_usd"] >= 3.0
        assert not fake_stripe.InvoiceItem.create.called
        row_after = await db.cto_maxx_usage.find_one({"user_id": u1, "month": bucket})
        assert row_after["overage_count"] == 4, "report must never reset the counter"


class TestIncrMaxxUsageAtomicOverageDecision:
    async def test_fifty_concurrent_calls_overage_count_exactly_matches_formula(self, db, monkeypatch):
        """N=50 concurrent calls for a tier with cap=100... use a
        tiny cap so the overage boundary is actually crossed within
        50 calls: patch MAXX_MONTHLY_LIMITS so this user's tier has
        cap=20. Expected overage_count == max(0, 50 - 20) == 30,
        EXACTLY — any race-induced drift fails this."""
        from services import usage
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        monkeypatch.setitem(usage.MAXX_MONTHLY_LIMITS, "pro", 20)
        u = _uid("raceincr")
        await db.dev_users.insert_one({"user_id": u, "tier": "pro"})
        monkeypatch.setattr(usage, "require_db", lambda: db)

        await asyncio.gather(*(usage.incr_maxx_usage(u) for _ in range(50)))

        bucket = _current_ym()
        row = await db.cto_maxx_usage.find_one({"user_id": u, "month": bucket})
        assert row["count"] == 50
        assert row["overage_count"] == 30, (
            f"expected exactly max(0, 50-20)=30, got {row['overage_count']} — "
            f"a TOCTOU race between the capped-check read and the increment"
        )

    async def test_unlimited_tier_never_sets_overage_count(self, db, monkeypatch):
        from services import usage
        u = _uid("unlimited")
        await db.dev_users.insert_one({"user_id": u, "tier": "team"})
        monkeypatch.setattr(usage, "require_db", lambda: db)
        await asyncio.gather(*(usage.incr_maxx_usage(u) for _ in range(10)))
        bucket = _current_ym()
        row = await db.cto_maxx_usage.find_one({"user_id": u, "month": bucket})
        assert row["count"] == 10
        assert not row.get("overage_count")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
