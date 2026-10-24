"""tests/test_regression_iter2026_11b_pricing_engine_and_fifo_batches.py

Master Prompt v2 Part 2 — the real charging equation (services.
pricing_engine), FIFO per-payment batches layered onto services.wallet's
existing atomic bucket spend, and the resulting rollover/clawback
behavior changes.

Covers:
  1. services.pricing_engine: the equation, micro-token rounding,
     pricing_versions CRUD, the rolling-7-day-average job, plan_capacity.
  2. services.wallet FIFO batches: grant() creates a batch, hold()
     consumes oldest-first, release()/settle() restore batch-level
     attribution, claw_back_batch() is precise to ONE payment.
  3. settle()'s new actual_cost_usd path: real-cost charging, the held
     amount as a hard ceiling, the 3x spike guard flag, release()'s
     failed_run_cost field.
  4. run_monthly_grant_job's rollover: ADDS on top, never zeroes the
     plan bucket (the pre-Part-2 "expire and replace" behavior).

Real local Mongo — same rationale/harness pattern as
test_regression_iter2026_10h (concurrency/atomicity claims need a real
engine, not a dict mock).
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
        await db.pricing_versions.delete_many({"actor": {"$regex": "^wallet_test_"}})
        await db.action_cost_stats.delete_many({})


class TestPricingEquation:
    async def test_basic_equation_matches_formula(self):
        from services.pricing_engine import tokens_for_actual_cost
        # $0.10 real cost, 50% margin -> 0.10 * 100 / (1 - 0.5) = 20 tokens
        assert tokens_for_actual_cost(0.10, margin=0.5) == 20

    async def test_zero_cost_is_zero_tokens(self):
        from services.pricing_engine import tokens_for_actual_cost
        assert tokens_for_actual_cost(0, margin=0.5) == 0

    async def test_tiny_cost_rounds_to_nearest_whole_token_not_forced_up(self):
        """PRE-STEP (b), Master Prompt v2 continuation — an earlier
        version of this function forced a minimum charge of 1 whole
        token for ANY positive real cost, on the theory that a $0
        charge for real spend was worse than a tiny overcharge. The
        user corrected this: that forced minimum silently overcharges
        a cheap action by orders of magnitude (a $0.0005 run costing
        the same as a run 1000x more expensive). The equation must
        round to the NEAREST whole token like any other number — which
        can legitimately be 0 for a genuinely tiny cost. Only the
        separate failed_run_cost/actual_cost_usd ledger fields (which
        are never rounded) carry the true cost for margin reporting;
        rounding to 0 charged tokens here does not lose that figure."""
        from services.pricing_engine import tokens_for_actual_cost
        # $0.0005 * 100 / (1 - 0.5) = 0.1 tokens -> rounds to 0, NOT 1.
        assert tokens_for_actual_cost(0.0005, margin=0.5) == 0
        # A cost that genuinely rounds UP on its own merits still does:
        # $0.003 * 100 / (1 - 0.5) = 0.6 tokens -> rounds to 1.
        assert tokens_for_actual_cost(0.003, margin=0.5) == 1
        # A cost that rounds DOWN on its own merits (no floor applied):
        # $0.002 * 100 / (1 - 0.5) = 0.4 tokens -> rounds to 0.
        assert tokens_for_actual_cost(0.002, margin=0.5) == 0

    async def test_many_tiny_charges_each_round_independently(self):
        """Documents the accepted tradeoff of the chosen (minimal,
        non-breaking) fix: repeated tiny charges that each round to 0
        do NOT accumulate into an eventual whole-token charge — each
        settle() call is independent. The true cost is still visible
        to admins via actual_cost_usd on every settle ledger row."""
        from services.pricing_engine import tokens_for_actual_cost
        results = [tokens_for_actual_cost(0.0005, margin=0.5) for _ in range(1000)]
        assert all(r == 0 for r in results)

    async def test_bad_margin_raises(self):
        from services.pricing_engine import tokens_for_actual_cost
        with pytest.raises(ValueError):
            tokens_for_actual_cost(1.0, margin=1.0)
        with pytest.raises(ValueError):
            tokens_for_actual_cost(1.0, margin=-0.1)


class TestPricingVersions:
    async def test_defaults_when_none_set(self, db):
        from services.pricing_engine import (
            DEFAULT_MARGIN,
            get_effective_pricing_version,
        )
        v = await get_effective_pricing_version(db)
        assert v["margin"] == DEFAULT_MARGIN
        assert v["_id"] == "default"

    async def test_set_version_becomes_effective(self, db):
        from services.pricing_engine import (
            get_effective_pricing_version,
            set_pricing_version,
        )
        await set_pricing_version(db, margin=0.6, actor="wallet_test_admin")
        v = await get_effective_pricing_version(db)
        assert v["margin"] == 0.6

    async def test_future_effective_from_not_yet_effective(self, db):
        import time

        from services.pricing_engine import (
            get_effective_pricing_version,
            set_pricing_version,
        )
        await set_pricing_version(
            db, margin=0.6, actor="wallet_test_admin",
            effective_from=time.time() + 3600,
        )
        v = await get_effective_pricing_version(db)
        assert v["margin"] != 0.6, "a future-dated version must not be effective yet"

    async def test_most_recent_past_version_wins(self, db):
        import time

        from services.pricing_engine import (
            get_effective_pricing_version,
            set_pricing_version,
        )
        await set_pricing_version(
            db, margin=0.3, actor="wallet_test_admin", effective_from=time.time() - 100,
        )
        await set_pricing_version(
            db, margin=0.7, actor="wallet_test_admin", effective_from=time.time() - 50,
        )
        v = await get_effective_pricing_version(db)
        assert v["margin"] == 0.7


class TestRollingCostJob:
    async def test_no_data_yields_none(self, db):
        from services.pricing_engine import compute_rolling_average_cost
        avg = await compute_rolling_average_cost(db, "fix_test_action_nodata", days=7)
        assert avg is None

    async def test_average_from_settle_and_release_rows(self, db):
        from services.pricing_engine import compute_rolling_average_cost
        from services.wallet import _now
        action = "fix"
        await db.wallet_ledger.insert_many([
            {"_id": f"wallet_test_s1_{uuid.uuid4().hex[:6]}", "user_id": "x",
             "kind": "settle", "action": action, "actual_cost_usd": 0.10,
             "created_at": _now()},
            {"_id": f"wallet_test_s2_{uuid.uuid4().hex[:6]}", "user_id": "x",
             "kind": "settle", "action": action, "actual_cost_usd": 0.20,
             "created_at": _now()},
            {"_id": f"wallet_test_r1_{uuid.uuid4().hex[:6]}", "user_id": "x",
             "kind": "release", "action": action, "failed_run_cost": 0.30,
             "created_at": _now()},
        ])
        avg = await compute_rolling_average_cost(db, action, days=7)
        assert avg == pytest.approx(0.20, abs=1e-9)

    async def test_run_rolling_cost_update_writes_action_cost_stats(self, db):
        from services.pricing_engine import run_rolling_cost_update
        from services.wallet import _now
        await db.wallet_ledger.insert_one({
            "_id": f"wallet_test_s3_{uuid.uuid4().hex[:6]}", "user_id": "x",
            "kind": "settle", "action": "chat_turn", "actual_cost_usd": 0.01,
            "created_at": _now(),
        })
        res = await run_rolling_cost_update(db)
        assert res["dry_run"] is False
        row = await db.action_cost_stats.find_one({"_id": "chat_turn"})
        assert row is not None
        assert row["avg_cost_usd_7d"] == pytest.approx(0.01, abs=1e-9)

    async def test_dry_run_does_not_write(self, db):
        from services.pricing_engine import run_rolling_cost_update
        from services.wallet import _now
        await db.wallet_ledger.insert_one({
            "_id": f"wallet_test_s4_{uuid.uuid4().hex[:6]}", "user_id": "x",
            "kind": "settle", "action": "loop_run", "actual_cost_usd": 0.05,
            "created_at": _now(),
        })
        await run_rolling_cost_update(db, dry_run=True)
        row = await db.action_cost_stats.find_one({"_id": "loop_run"})
        assert row is None


class TestPlanCapacity:
    async def test_fallback_to_price_table_when_no_live_data(self):
        from services.pricing_engine import plan_capacity
        # fix costs 12 flat tokens -> 900 plan tokens buys 75 fixes
        assert plan_capacity(900, "fix") == 75.0

    async def test_unknown_action_returns_none(self):
        from services.pricing_engine import plan_capacity
        assert plan_capacity(900, "not_a_real_action") is None


class TestWalletBatches:
    async def test_grant_creates_a_batch(self, db):
        from services.wallet import grant
        u = _uid("batchgrant")
        key = f"grant_{u}"
        await grant(db, user_id=u, bucket="purchased", amount=500,
                    idempotency_key=key, source="test")
        w = await db.wallets.find_one({"_id": u})
        batches = w.get("batches") or []
        assert len(batches) == 1
        assert batches[0]["batch_id"] == key
        assert batches[0]["amount_remaining"] == 500
        assert batches[0]["bucket"] == "purchased"

    async def test_hold_consumes_oldest_batch_first(self, db, monkeypatch):
        from services.wallet import _consume_batches_fifo, grant
        u = _uid("fifo")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"old_{u}", source="test")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"new_{u}", source="test")
        taken = await _consume_batches_fifo(db, u, "purchased", 60)
        assert taken == {f"old_{u}": 60}, "FIFO must drain the OLDEST batch first"

        w = await db.wallets.find_one({"_id": u})
        by_id = {b["batch_id"]: b["amount_remaining"] for b in w["batches"]}
        assert by_id[f"old_{u}"] == 40
        assert by_id[f"new_{u}"] == 100

    async def test_hold_spans_multiple_batches_when_oldest_is_insufficient(self, db):
        from services.wallet import _consume_batches_fifo, grant
        u = _uid("fifospan")
        await grant(db, user_id=u, bucket="purchased", amount=30,
                    idempotency_key=f"old_{u}", source="test")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"new_{u}", source="test")
        taken = await _consume_batches_fifo(db, u, "purchased", 50)
        assert taken == {f"old_{u}": 30, f"new_{u}": 20}

    async def test_hold_and_release_round_trips_batch_amounts(self, db):
        from services.wallet import assert_wallet, get_balance, release
        u = _uid("holdrelease")
        from services.wallet import grant
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        monkeypatch_env = os.environ.get("TOKEN_BILLING_MODE")
        os.environ["TOKEN_BILLING_MODE"] = "on"
        try:
            await assert_wallet(db, user_id=u, action="fix", run_id=f"run_{u}",
                                 idempotency_key=f"hold_{u}")
            w = await db.wallets.find_one({"_id": u})
            assert w["batches"][0]["amount_remaining"] == 100 - 12  # fix=12
            await release(db, user_id=u, run_id=f"run_{u}",
                          idempotency_key=f"rel_{u}")
            w = await db.wallets.find_one({"_id": u})
            assert w["batches"][0]["amount_remaining"] == 100
            bal = await get_balance(db, u)
            assert bal["purchased"] == 100
        finally:
            if monkeypatch_env is None:
                os.environ.pop("TOKEN_BILLING_MODE", None)
            else:
                os.environ["TOKEN_BILLING_MODE"] = monkeypatch_env

    async def test_settle_partial_refund_restores_batch_proportionally(self, db):
        from services.wallet import grant, hold, settle
        u = _uid("settlerefund")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="loop_run", run_id=f"run_{u}",
                   idempotency_key=f"hold_{u}")  # loop_run=40
        w = await db.wallets.find_one({"_id": u})
        assert w["batches"][0]["amount_remaining"] == 60
        # Settle at a lower real cost -> refund the difference back to
        # the SAME batch it came from.
        await settle(db, user_id=u, run_id=f"run_{u}", actual_tokens=10,
                     idempotency_key=f"settle_{u}")
        w = await db.wallets.find_one({"_id": u})
        assert w["batches"][0]["amount_remaining"] == 90, (
            "settle's refund must restore the batch, not just the aggregate bucket"
        )


class TestClawBackBatch:
    async def test_targets_exact_batch_not_others(self, db):
        from services.wallet import claw_back_batch, get_balance, grant
        u = _uid("clawexact")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"batch_a_{u}", source="test")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"batch_b_{u}", source="test")
        res = await claw_back_batch(
            db, user_id=u, batch_id=f"batch_a_{u}", amount=40,
            idempotency_key=f"claw_{u}", source="test",
        )
        assert res == {"ok": True, "idempotent_replay": False, "removed": 40, "shortfall": 0}
        w = await db.wallets.find_one({"_id": u})
        by_id = {b["batch_id"]: b["amount_remaining"] for b in w["batches"]}
        assert by_id[f"batch_a_{u}"] == 60
        assert by_id[f"batch_b_{u}"] == 100, "clawback must never touch a DIFFERENT batch"
        bal = await get_balance(db, u)
        assert bal["purchased"] == 160

    async def test_reports_shortfall_when_mostly_spent(self, db):
        from services.wallet import claw_back_batch, grant
        u = _uid("clawshort")
        await grant(db, user_id=u, bucket="purchased", amount=20,
                    idempotency_key=f"batch_{u}", source="test")
        res = await claw_back_batch(
            db, user_id=u, batch_id=f"batch_{u}", amount=100,
            idempotency_key=f"claw_{u}", source="test",
        )
        assert res["removed"] == 20
        assert res["shortfall"] == 80

    async def test_unknown_batch_id_is_full_shortfall_no_mutation(self, db):
        from services.wallet import claw_back_batch, get_balance, grant
        u = _uid("clawunknown")
        await grant(db, user_id=u, bucket="purchased", amount=50,
                    idempotency_key=f"batch_{u}", source="test")
        res = await claw_back_batch(
            db, user_id=u, batch_id="nonexistent_batch", amount=30,
            idempotency_key=f"claw_{u}", source="test",
        )
        assert res["removed"] == 0
        assert res["shortfall"] == 30
        bal = await get_balance(db, u)
        assert bal["purchased"] == 50, "an unknown batch id must never touch real balances"

    async def test_idempotent_on_duplicate_key(self, db):
        from services.wallet import claw_back_batch, get_balance, grant
        u = _uid("clawdup")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"batch_{u}", source="test")
        key = f"claw_{u}"
        await claw_back_batch(db, user_id=u, batch_id=f"batch_{u}", amount=30,
                               idempotency_key=key, source="test")
        res2 = await claw_back_batch(db, user_id=u, batch_id=f"batch_{u}", amount=30,
                                     idempotency_key=key, source="test")
        assert res2["idempotent_replay"] is True
        bal = await get_balance(db, u)
        assert bal["purchased"] == 70, "a retried clawback must not double-remove"


class TestSettleActualCost:
    async def test_charges_real_cost_when_cheaper_than_estimate(self, db):
        from services.pricing_engine import set_pricing_version
        from services.wallet import grant, hold, settle
        u = _uid("realcheap")
        await set_pricing_version(db, margin=0.0, actor="wallet_test_admin")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="loop_run", run_id=f"run_{u}",
                   idempotency_key=f"hold_{u}")  # held = 40 tokens = $0.40
        # Real cost $0.05 margin=0 -> 5 tokens, well under the 40-token hold.
        res = await settle(db, user_id=u, run_id=f"run_{u}",
                           actual_cost_usd=0.05, idempotency_key=f"settle_{u}")
        assert res["charged"] == 5
        assert res["refunded"] == 35

    async def test_never_charges_more_than_the_held_ceiling(self, db):
        from services.pricing_engine import set_pricing_version
        from services.wallet import grant, hold, settle
        u = _uid("realexpensive")
        await set_pricing_version(db, margin=0.0, actor="wallet_test_admin")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"hold_{u}")  # held = 12 tokens
        # A wildly expensive real run ($5 -> 500 tokens) must still never
        # charge more than the 12-token hold.
        res = await settle(db, user_id=u, run_id=f"run_{u}",
                           actual_cost_usd=5.0, idempotency_key=f"settle_{u}")
        assert res["charged"] == 12
        assert res["refunded"] == 0

    async def test_spike_guard_flags_when_actual_is_3x_estimate(self, db):
        from services.pricing_engine import set_pricing_version
        from services.wallet import grant, hold, settle
        u = _uid("spike")
        await set_pricing_version(db, margin=0.0, actor="wallet_test_admin")
        await grant(db, user_id=u, bucket="purchased", amount=1000,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"hold_{u}")  # held = 12 tokens = $0.12 estimate
        # $0.40 actual is >= 3x the $0.12 estimate.
        await settle(db, user_id=u, run_id=f"run_{u}",
                     actual_cost_usd=0.40, idempotency_key=f"settle_{u}")
        flag = await db.billing_flags.find_one(
            {"user_id": u, "flag_type": "cost_spike"},
        )
        assert flag is not None
        assert flag["detail"]["action"] == "fix"

    async def test_no_spike_flag_under_the_multiple(self, db):
        from services.pricing_engine import set_pricing_version
        from services.wallet import grant, hold, settle
        u = _uid("nospike")
        await set_pricing_version(db, margin=0.0, actor="wallet_test_admin")
        await grant(db, user_id=u, bucket="purchased", amount=1000,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"hold_{u}")  # held = 12 tokens = $0.12 estimate
        await settle(db, user_id=u, run_id=f"run_{u}",
                     actual_cost_usd=0.15, idempotency_key=f"settle_{u}")
        flag = await db.billing_flags.find_one(
            {"user_id": u, "flag_type": "cost_spike"},
        )
        assert flag is None

    async def test_requires_exactly_one_of_actual_tokens_or_cost(self, db):
        from services.wallet import grant, hold, settle
        u = _uid("xorcheck")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"hold_{u}")
        with pytest.raises(ValueError):
            await settle(db, user_id=u, run_id=f"run_{u}", idempotency_key=f"settle_{u}")
        with pytest.raises(ValueError):
            await settle(db, user_id=u, run_id=f"run_{u}", actual_tokens=1,
                         actual_cost_usd=0.01, idempotency_key=f"settle_{u}2")


class TestReleaseFailedRunCost:
    async def test_release_records_failed_run_cost_without_charging(self, db):
        from services.wallet import get_balance, grant, hold, release
        u = _uid("failedcost")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"hold_{u}")
        await release(db, user_id=u, run_id=f"run_{u}",
                      idempotency_key=f"rel_{u}", reason="llm_call_failed",
                      failed_run_cost=0.07)
        bal = await get_balance(db, u)
        assert bal["purchased"] == 100, "a failed run is never charged, regardless of real cost"
        row = await db.wallet_ledger.find_one({"_id": f"rel_{u}"})
        assert row["failed_run_cost"] == 0.07


class TestMonthlyGrantRollover:
    """PRE-STEP (d) — run_monthly_grant_job only grants with billing
    mode "on" (see test_regression_iter2026_11c for the off/shadow
    no-op coverage); every test here sets it explicitly."""

    async def test_grant_adds_on_top_never_zeroes_existing_balance(self, db, monkeypatch):
        from services.wallet import get_balance, run_monthly_grant_job
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("rollover")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        # Simulate last month's leftover balance sitting in the plan bucket.
        from services.wallet import grant
        await grant(db, user_id=u, bucket="plan", amount=500,
                    idempotency_key=f"leftover_{u}", source="test")
        res = await run_monthly_grant_job(db)
        applied = {a["user_id"]: a for a in res["applied"]}
        assert u in applied
        bal = await get_balance(db, u)
        assert bal["plan"] == 500 + 900, (
            "Part 2 rollover: the new month's grant must ADD on top of "
            "whatever was left, never replace/zero it"
        )

    async def test_idempotent_within_the_same_month(self, db, monkeypatch):
        from services.wallet import get_balance, run_monthly_grant_job
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("rolloverdup")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        await run_monthly_grant_job(db)
        await run_monthly_grant_job(db)  # same month, second run
        bal = await get_balance(db, u)
        assert bal["plan"] == 900, "a second run in the same month must not double-grant"

    async def test_checkout_instant_grant_key_matches_cron_key_same_month(self, db, monkeypatch):
        """Closes the latent gap: before Part 2, the checkout-triggered
        instant first-month grant used a DIFFERENT idempotency key than
        the monthly cron, so a mid-month signup could be double-granted
        by the cron later that month. Both now key off
        `monthly_grant:{user_id}:{ym}`."""
        from services.wallet import (
            _year_month,
            get_balance,
            grant,
            run_monthly_grant_job,
        )
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("unifiedkey")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        ym = _year_month()
        # Simulate the checkout.session.completed instant grant.
        await grant(db, user_id=u, bucket="plan", amount=900,
                    idempotency_key=f"monthly_grant:{u}:{ym}",
                    source="stripe_webhook:starter")
        # The SAME month's cron run must now see this as already granted.
        res = await run_monthly_grant_job(db)
        applied_ids = {a["user_id"] for a in res["applied"]}
        assert u not in applied_ids, "cron must not double-grant the signup month"
        bal = await get_balance(db, u)
        assert bal["plan"] == 900


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
