"""tests/test_regression_iter2026_12a_microtoken_precision.py

PRE-STEP (a), Master Prompt v2 continuation — the ROUNDING LEAK fix.

The prior turn's "round to nearest whole token, allow 0" rule meant
every settle() call with a real cost under ~$0.004 (at a typical
margin) deducted ZERO tokens — a genuinely free ride for high-frequency
cheap actions (chat turns, classify, extract, summarize) while the
platform still paid the real provider cost. This file proves the fix:
wallet buckets now carry an EXACT micro-token remainder (1 token =
1000 micro-tokens) alongside the existing whole-token integer fields,
so the DEDUCTION itself is always exact — only `get_balance()`'s
legacy integer fields (meant for display/backward-compat) round.

Worked-example note: the master prompt's own text says "$0.0005 at
margin 0.2 must deduct 625 micro-tokens, not 0 and not 1000." Recomputing
from the SAME equation (tokens = cost_usd*100/(1-margin)) and the
SAME stated ratio (1 token = 1000 micro-tokens) actually gives:
0.0005 * 100 / 0.8 = 0.0625 tokens = 62.5 micro-tokens -> rounds to
62 or 63, not 625 (625 would require 1 token = 10,000 micro-tokens,
contradicting the explicitly stated 1000 ratio). Read in context
("not 0 and not 1000") this reads as a typo for "62.5", not a real
10x-different target — 0 is today's leak, 1000 is the OLD forced-
minimum-of-a-whole-token bug under the SAME 1000:1 ratio. Implemented
and tested against the mathematically consistent value (62 or 63);
flagged here plainly rather than silently guessed past.

Real local Mongo — the atomicity/concurrency/no-drift claims are
statements about MongoDB's own pipeline-update guarantees, not
provable against a dict mock.
"""
from __future__ import annotations

import asyncio
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
    yield database
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})


class TestMicroTokenEquation:
    async def test_tiny_cost_deducts_exact_micro_not_zero_not_a_whole_token(self):
        from services.pricing_engine import micro_tokens_for_actual_cost
        # 0.0005 * 100 / (1 - 0.2) = 0.0625 tokens = 62.5 micro-tokens.
        micro = micro_tokens_for_actual_cost(0.0005, margin=0.2)
        assert micro not in (0, 1000), "must not leak to 0 or force a whole token (1000 micro)"
        assert micro in (62, 63), f"expected 62 or 63 micro-tokens (62.5 rounded), got {micro}"

    async def test_zero_cost_is_zero_micro(self):
        from services.pricing_engine import micro_tokens_for_actual_cost
        assert micro_tokens_for_actual_cost(0, margin=0.5) == 0

    async def test_negative_cost_is_zero_micro(self):
        from services.pricing_engine import micro_tokens_for_actual_cost
        assert micro_tokens_for_actual_cost(-5.0, margin=0.5) == 0

    async def test_huge_cost_does_not_overflow(self):
        from services.pricing_engine import micro_tokens_for_actual_cost
        micro = micro_tokens_for_actual_cost(1_000_000.0, margin=0.5)
        assert isinstance(micro, int)
        assert micro < 2**63


class TestSingleTinyCallDeductsExactMicro:
    async def test_single_tiny_settle_leaves_exact_remainder(self, db):
        from services.pricing_engine import set_pricing_version
        from services.wallet import get_balance, grant, hold, settle
        u = _uid("tiny1")
        await set_pricing_version(db, margin=0.2, actor="wallet_test_admin")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="chat_turn", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")  # chat_turn = 2 tokens = 2000 micro
        res = await settle(db, user_id=u, run_id=f"run_{u}",
                           actual_cost_usd=0.0005, idempotency_key=f"settle_{u}")
        # Exact charge is 62 or 63 micro, NOT 0 and NOT 1000 (a forced whole token).
        assert res["charged_micro"] in (62, 63)
        assert res["charged_micro"] not in (0, 1000)

        bal = await get_balance(db, u)
        # True balance, in micro, must reflect the EXACT deduction:
        # 100 tokens (100000 micro) - exact_charge.
        expected_spendable_micro = 100_000 - res["charged_micro"]
        assert bal["spendable_micro"] == expected_spendable_micro
        # Legacy whole-token field rounds the TRUE value: 99.9375 or
        # 99.9370 tokens both round to 100, never literally drop to 99
        # or to 0 at the display level either.
        assert bal["purchased"] == round(expected_spendable_micro / 1000) == 100


class TestTenThousandTinyCallsNoDrift:
    async def test_ten_thousand_tiny_calls_sum_to_the_exact_total(self, db):
        """THE core no-drift/no-leak proof. 10,000 identical $0.0005
        calls against a large starting balance must deduct EXACTLY
        10,000 * micro_tokens_for_actual_cost(0.0005, margin) micro-
        tokens in total — no rounding loss accumulated across calls,
        no leak to 0."""
        from services.pricing_engine import (
            micro_tokens_for_actual_cost,
            set_pricing_version,
        )
        from services.wallet import get_balance, grant, hold, settle
        u = _uid("tenk")
        margin = 0.2
        await set_pricing_version(db, margin=margin, actor="wallet_test_admin")
        starting_tokens = 1000
        await grant(db, user_id=u, bucket="purchased", amount=starting_tokens,
                    idempotency_key=f"g_{u}", source="test")

        per_call_micro = micro_tokens_for_actual_cost(0.0005, margin=margin)
        n = 10_000
        total_charged_micro = 0
        for i in range(n):
            run_id = f"run_{u}_{i}"
            await hold(db, user_id=u, action="chat_turn", run_id=run_id,
                       idempotency_key=f"h_{u}_{i}")
            res = await settle(db, user_id=u, run_id=run_id,
                               actual_cost_usd=0.0005, idempotency_key=f"s_{u}_{i}")
            total_charged_micro += res["charged_micro"]

        assert total_charged_micro == n * per_call_micro, (
            "sum of individually-charged micro-tokens must equal n * per-call "
            "charge EXACTLY — any mismatch is drift/leak"
        )
        bal = await get_balance(db, u)
        expected_spendable_micro = starting_tokens * 1000 - total_charged_micro
        assert bal["spendable_micro"] == expected_spendable_micro, (
            "the wallet's actual stored balance must match the exact sum of "
            "charges — no silent rounding loss in storage"
        )


class TestBalanceAPIConsistency:
    async def test_balance_fields_stay_internally_consistent_with_micro(self, db):
        from services.pricing_engine import set_pricing_version
        from services.wallet import get_balance, grant, hold, settle
        u = _uid("apiconsistency")
        await set_pricing_version(db, margin=0.2, actor="wallet_test_admin")
        await grant(db, user_id=u, bucket="purchased", amount=50,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="chat_turn", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")
        await settle(db, user_id=u, run_id=f"run_{u}", actual_cost_usd=0.0005,
                     idempotency_key=f"settle_{u}")
        bal = await get_balance(db, u)
        # The legacy `purchased` field is a ROUNDED display of the
        # exact `purchased_micro` value — not a raw bucket integer the
        # remainder gets added on top of (that would double-count:
        # micro is already remainder-inclusive).
        assert bal["purchased"] == round(bal["purchased_micro"] / 1000)
        assert bal["spendable_micro"] == bal["purchased_micro"]
        assert 0 <= bal["purchased_micro_remainder"] < 1000
        # Exact charge for this one call must land in (0, 1000) — a
        # genuine fraction of a token, never 0 (the old leak) or a
        # full 1000 (the old forced minimum).
        assert 0 < (50_000 - bal["purchased_micro"]) < 1000


class TestConcurrentTinyCallBurst:
    async def test_concurrent_burst_of_tiny_calls_never_drifts_or_goes_negative(self, db):
        """asyncio.gather burst of tiny-cost settles — the AGGREGATE
        whole-token spend (hold) is already proven atomic elsewhere;
        this proves the MICRO remainder carry (_apply_micro_refund)
        doesn't lose or double-count fractional tokens under
        concurrent settles for DIFFERENT runs on the SAME wallet."""
        from services.pricing_engine import (
            micro_tokens_for_actual_cost,
            set_pricing_version,
        )
        from services.wallet import get_balance, grant, hold, settle
        u = _uid("burst")
        margin = 0.2
        await set_pricing_version(db, margin=margin, actor="wallet_test_admin")
        starting_tokens = 200
        await grant(db, user_id=u, bucket="purchased", amount=starting_tokens,
                    idempotency_key=f"g_{u}", source="test")

        n = 50
        for i in range(n):
            await hold(db, user_id=u, action="chat_turn", run_id=f"run_{u}_{i}",
                       idempotency_key=f"h_{u}_{i}")

        async def _settle(i):
            return await settle(
                db, user_id=u, run_id=f"run_{u}_{i}", actual_cost_usd=0.0005,
                idempotency_key=f"s_{u}_{i}",
            )

        results = await asyncio.gather(*[_settle(i) for i in range(n)])
        per_call_micro = micro_tokens_for_actual_cost(0.0005, margin=margin)
        total_charged = sum(r["charged_micro"] for r in results)
        assert total_charged == n * per_call_micro

        bal = await get_balance(db, u)
        assert bal["spendable_micro"] == starting_tokens * 1000 - total_charged
        assert bal["spendable_micro"] >= 0
        assert 0 <= bal["purchased_micro_remainder"] < 1000


class TestNoForcedMinimum:
    async def test_many_tiny_calls_each_assessed_independently_no_forced_floor(self, db):
        """Documents explicitly: there is still no per-call forced
        minimum (that was the ORIGINAL bug this whole fix traces back
        to) — the fix is exactness at MICRO resolution, not a new
        floor at a different scale."""
        from services.pricing_engine import micro_tokens_for_actual_cost
        results = [micro_tokens_for_actual_cost(0.0005, margin=0.2) for _ in range(1000)]
        assert all(r in (62, 63) for r in results)
        assert all(r != 0 for r in results), "not a leak to 0"
        assert all(r != 1000 for r in results), "not a forced whole token either"


class TestHeldCeilingStillAppliesInMicro:
    async def test_huge_real_cost_still_capped_at_the_held_micro_ceiling(self, db):
        from services.pricing_engine import set_pricing_version
        from services.wallet import get_balance, grant, hold, settle
        u = _uid("ceilingmicro")
        await set_pricing_version(db, margin=0.0, actor="wallet_test_admin")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")  # held = 12 tokens = 12000 micro
        res = await settle(db, user_id=u, run_id=f"run_{u}", actual_cost_usd=5.0,
                           idempotency_key=f"settle_{u}")
        assert res["charged_micro"] == 12_000, "must cap at the held ceiling, in micro"
        assert res["charged"] == 12
        bal = await get_balance(db, u)
        assert bal["spendable_micro"] == (100 - 12) * 1000


class TestDashboardRevenueUsesMicro:
    async def test_dashboard_revenue_does_not_undercount_fractional_charges(self, db):
        """A follow-on bug caught while fixing (a): settle()'s legacy
        `amount` field now FLOORS a fractional charge to whole tokens
        (e.g. a real 62-micro-token charge floors to amount=0) — the
        admin dashboard's revenue aggregation summed exactly that
        field, so 1000 tiny real charges would have reported ~$0
        revenue despite genuinely charging real money. Fixed by
        summing `amount_micro` instead (see services.pricing_engine.
        compute_dashboard)."""
        from services.pricing_engine import (
            compute_dashboard,
            micro_tokens_for_actual_cost,
            set_pricing_version,
        )
        from services.wallet import grant, hold, settle
        u = _uid("dashrevenue")
        margin = 0.2
        await set_pricing_version(db, margin=margin, actor="wallet_test_admin")
        await grant(db, user_id=u, bucket="purchased", amount=1000,
                    idempotency_key=f"g_{u}", source="test")

        n = 20
        for i in range(n):
            run_id = f"run_{u}_{i}"
            await hold(db, user_id=u, action="chat_turn", run_id=run_id,
                       idempotency_key=f"h_{u}_{i}")
            await settle(db, user_id=u, run_id=run_id, actual_cost_usd=0.0005,
                        idempotency_key=f"s_{u}_{i}")

        per_call_micro = micro_tokens_for_actual_cost(0.0005, margin=margin)
        expected_revenue_usd = n * per_call_micro / 100_000  # micro-tokens -> USD

        data = await compute_dashboard(db)
        row = next(r for r in data["by_action"] if r["action"] == "chat_turn")
        assert row["revenue_usd"] == pytest.approx(expected_revenue_usd, abs=1e-6)
        assert row["revenue_usd"] > 0, (
            "summing the legacy floored `amount` field would report ~$0 here"
        )

        user_row = next(r for r in data["by_user_top50"] if r["user_id"] == u)
        assert user_row["revenue_usd"] == pytest.approx(expected_revenue_usd, abs=1e-6)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
