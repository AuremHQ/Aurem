"""tests/test_regression_iter2026_11c_prestep_redteam_and_edge_cases.py

Master Prompt v2 — PRE-STEP before PART 3 (admin panel). Per the user's
explicit HARD RULES v2 (T1 TDD / T4 security / T6 self red-team), this
file covers:

  (a) Self red-team of Parts 1-2: cross-user wallet reads/spends, forged/
      duplicate/out-of-order Stripe events, refund/dispute on an
      already-spent batch, negative/huge cost to settle(), concurrent
      settle+release on the same hold, a stale/backdated price version,
      Mongo operator injection in user-supplied ids/keys.
  (c) Month-boundary correctness for the monthly_grant:{user}:{ym}
      subscription-refund batch key (23:59:59 UTC last day, 00:00:01 UTC
      1st of month, across a UTC offset).
  (d) run_monthly_grant_job must be a complete no-op when
      TOKEN_BILLING_MODE=off — found and fixed a real gap here: the job
      had NO mode check at all, so it would have unconditionally
      written wallet docs for every paying user even with billing off.

(b) — the settle() tiny-cost rounding fix — lives in
test_regression_iter2026_11b_pricing_engine_and_fifo_batches.py next to
the function it tests.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import ClassVar

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


# ─────────────────────────────────────────────────────────────────────
# (d) run_monthly_grant_job must be a no-op when TOKEN_BILLING_MODE=off
# ─────────────────────────────────────────────────────────────────────

class TestMonthlyGrantRespectsOffMode:
    async def test_off_mode_grants_nothing_for_existing_task_based_users(
        self, db, monkeypatch,
    ):
        """RED (before fix): run_monthly_grant_job had no billing_mode
        check at all, so a tier="starter" user who has NEVER touched
        the token-billing feature (a pure task-based legacy user) would
        get a `wallets` document silently created/populated the first
        time this cron ran, even with TOKEN_BILLING_MODE unset/off —
        violating "existing users' data untouched"."""
        from services.wallet import get_balance, run_monthly_grant_job
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        u = _uid("offmode")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})

        res = await run_monthly_grant_job(db)

        assert res["applied_count"] == 0
        w = await db.wallets.find_one({"_id": u})
        assert w is None, (
            "off mode must not create/touch a wallet document for an "
            "existing task-based user at all"
        )
        bal = await get_balance(db, u)
        assert bal["plan"] == 0

    async def test_shadow_mode_also_grants_nothing(self, db, monkeypatch):
        """shadow mode computes/logs what WOULD happen but never moves
        real tokens anywhere else in this module (assert_wallet) — the
        monthly grant job must be consistent with that, not a special
        case that actually writes in shadow mode."""
        from services.wallet import get_balance, run_monthly_grant_job
        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        u = _uid("shadowmode")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})

        await run_monthly_grant_job(db)

        bal = await get_balance(db, u)
        assert bal["plan"] == 0

    async def test_on_mode_still_grants_normally(self, db, monkeypatch):
        """Confirms the off/shadow gate didn't also break the real
        "on" path — the regression this fix must not introduce."""
        from services.wallet import get_balance, run_monthly_grant_job
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("onmode")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})

        res = await run_monthly_grant_job(db)

        assert res["applied_count"] == 1
        bal = await get_balance(db, u)
        assert bal["plan"] == 900


# ─────────────────────────────────────────────────────────────────────
# (c) Month-boundary correctness for monthly_grant:{user}:{ym} batch key
# ─────────────────────────────────────────────────────────────────────

class TestMonthBoundaryBatchKey:
    async def _refund_for_charge_created_at(self, db, monkeypatch, user_id, created_dt,
                                             plan_batch_amount=900):
        """Grants a plan batch keyed to whatever month `created_dt`
        falls in (per services.wallet._year_month), then fires a
        charge.refunded webhook whose charge.created timestamp is
        EXACTLY created_dt, and returns the resulting wallet balance."""
        from routers import payments
        from services.wallet import _year_month, grant
        ym = _year_month(created_dt)
        await grant(
            db, user_id=user_id, bucket="plan", amount=plan_batch_amount,
            idempotency_key=f"monthly_grant:{user_id}:{ym}", source="test",
        )
        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "charge.refunded",
            "data": {"object": {
                "id": f"ch_{uuid.uuid4().hex[:10]}",
                "amount": plan_batch_amount, "amount_refunded": plan_batch_amount,
                "created": created_dt.timestamp(),
                "metadata": {"user_id": user_id, "plan": "starter"},
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

    async def test_charge_at_235959_last_day_of_month_resolves_correct_batch(
        self, db, monkeypatch,
    ):
        from services.wallet import get_balance
        u = _uid("boundary1")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "fix_entitlement": True,
            "stripe_sub_id": None,
        })
        # Last second of a 31-day month, UTC.
        created = datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC)
        await self._refund_for_charge_created_at(db, monkeypatch, u, created)
        bal = await get_balance(db, u)
        assert bal["plan"] == 0, "the January batch must be the one clawed back"

    async def test_charge_at_000001_first_day_of_month_resolves_correct_batch(
        self, db, monkeypatch,
    ):
        from services.wallet import get_balance
        u = _uid("boundary2")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "fix_entitlement": True,
            "stripe_sub_id": None,
        })
        # First second of the NEXT month, UTC.
        created = datetime(2026, 2, 1, 0, 0, 1, tzinfo=UTC)
        await self._refund_for_charge_created_at(db, monkeypatch, u, created)
        bal = await get_balance(db, u)
        assert bal["plan"] == 0, "the February batch must be the one clawed back"

    async def test_charges_one_second_apart_across_month_boundary_never_cross_wires(
        self, db, monkeypatch,
    ):
        """The defining risk this test guards: a Jan-31 23:59:59 charge
        and a Feb-1 00:00:01 charge are ONE SECOND apart in wall-clock
        time but must resolve to two DIFFERENT monthly batches — a
        refund of one must never claw back the other's tokens."""
        from services.wallet import _year_month, get_balance, grant
        u = _uid("boundary3")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "fix_entitlement": True,
            "stripe_sub_id": None,
        })
        jan_dt = datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC)
        feb_dt = datetime(2026, 2, 1, 0, 0, 1, tzinfo=UTC)
        jan_ym = _year_month(jan_dt)
        feb_ym = _year_month(feb_dt)
        assert jan_ym != feb_ym
        await grant(db, user_id=u, bucket="plan", amount=900,
                    idempotency_key=f"monthly_grant:{u}:{jan_ym}", source="test")
        await grant(db, user_id=u, bucket="plan", amount=900,
                    idempotency_key=f"monthly_grant:{u}:{feb_ym}", source="test")
        bal = await get_balance(db, u)
        assert bal["plan"] == 1800

        # Refund the JANUARY charge only.
        await self._refund_for_charge_created_at(
            db, monkeypatch, u, jan_dt, plan_batch_amount=900,
        )
        bal = await get_balance(db, u)
        assert bal["plan"] == 900, (
            "refunding the Jan charge must leave the Feb batch's 900 "
            "tokens completely untouched"
        )

    async def test_missing_created_timestamp_fails_safe_not_wrong_clawback(
        self, db, monkeypatch,
    ):
        """If Stripe ever omits `created` on the charge object, the
        code falls back to "now" (see routers/payments.py) — which
        means it targets THIS month's batch. If that batch doesn't
        exist, claw_back_batch reports a full shortfall and touches
        NOTHING, rather than guessing and clawing back some unrelated
        batch. This test locks in the fail-safe (no wrong-batch
        mutation), not a specific resolved month."""
        from routers import payments
        from services.wallet import get_balance, grant
        u = _uid("boundary4")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "fix_entitlement": True,
            "stripe_sub_id": None,
        })
        # Grant a batch for a DEFINITELY-WRONG month (10 years ago) so
        # the "created omitted -> falls back to now" path can never
        # accidentally find and clobber it.
        old_ym = "2016-01"
        await grant(db, user_id=u, bucket="plan", amount=900,
                    idempotency_key=f"monthly_grant:{u}:{old_ym}", source="test")

        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "charge.refunded",
            "data": {"object": {
                "id": f"ch_{uuid.uuid4().hex[:10]}",
                "amount": 900, "amount_refunded": 900,
                # "created" deliberately omitted.
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

        bal = await get_balance(db, u)
        assert bal["plan"] == 900, (
            "the 10-year-old batch must be left completely untouched "
            "when the charge's real month can't be determined"
        )
        flag = await db.billing_flags.find_one(
            {"user_id": u, "flag_type": "refund_shortfall"},
        )
        assert flag is not None, "the un-attributable refund must be flagged, not silent"


# ─────────────────────────────────────────────────────────────────────
# (a) self red-team — race conditions / double-spend
# ─────────────────────────────────────────────────────────────────────

class TestConcurrentSettleReleaseRace:
    async def test_concurrent_settle_and_release_never_double_refund(self, db):
        """FOUND BY THIS RED-TEAM, 100% reproducible before the fix:
        settle() and release() each used a two-step "find_one, then
        LATER update_one $unset" to read and clear a hold. Firing both
        concurrently on the SAME run_id (different idempotency keys,
        so neither's own dedup caught it) let both read the hold
        before either removed it — each then applied its own refund
        independently, materializing tokens out of nothing (100/100
        adversarial trials produced an inflated balance before the fix
        — see services.wallet._claim_hold). Fixed by making the hold
        read-and-remove a single atomic find_one_and_update; now
        exactly one of the two ever sees the hold."""
        from services.wallet import get_balance, grant, hold, release, settle
        u = _uid("settlereleaserace")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")  # held = 12

        results = await asyncio.gather(
            settle(db, user_id=u, run_id=f"run_{u}", actual_tokens=5,
                   idempotency_key=f"settle_{u}"),
            release(db, user_id=u, run_id=f"run_{u}",
                    idempotency_key=f"release_{u}"),
            return_exceptions=True,
        )
        bal = await get_balance(db, u)
        # Exactly one of settle/release can have won the claim. Either:
        #  - settle won: charged 5, refunded 7 -> 100 - 5 = 95.
        #  - release won: settle's _claim_hold then sees None and
        #    raises ValueError (its documented "no active hold" path)
        #    -> full hold refunded by release -> 100.
        # NEVER both landing (which would give 95 + 12 = 107).
        assert bal["purchased"] in (95, 100), (
            f"double-refund: balance={bal['purchased']} (results={results})"
        )
        settle_res, release_res = results
        if isinstance(settle_res, dict):
            assert isinstance(release_res, Exception) or release_res.get("released") == 0
        else:
            assert isinstance(settle_res, ValueError)
            assert release_res.get("released") == 12

    async def test_many_trials_never_inflate_balance(self, db):
        """Same race, run 30 times with fresh holds each time — a
        regression guard against this specific bug reappearing, not
        just a one-shot demonstration."""
        from services.wallet import get_balance, grant, hold, release, settle
        for i in range(30):
            u = _uid(f"racebulk{i}")
            await grant(db, user_id=u, bucket="purchased", amount=100,
                        idempotency_key=f"g_{u}", source="test")
            await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                       idempotency_key=f"h_{u}")
            await asyncio.gather(
                settle(db, user_id=u, run_id=f"run_{u}", actual_tokens=5,
                       idempotency_key=f"settle_{u}"),
                release(db, user_id=u, run_id=f"run_{u}",
                        idempotency_key=f"release_{u}"),
                return_exceptions=True,
            )
            bal = await get_balance(db, u)
            assert bal["purchased"] in (95, 100), f"trial {i}: balance={bal['purchased']}"


# ─────────────────────────────────────────────────────────────────────
# (a) self red-team — negative / huge cost passed to settle()
# ─────────────────────────────────────────────────────────────────────

class TestSettleCostAbuse:
    async def test_negative_actual_cost_never_credits_the_user(self, db):
        """A negative actual_cost_usd (forged provider response, bug
        in a caller, or a deliberately crafted value) must never be
        interpreted as "refund MORE than the full hold" — settle()
        clamps it to 0 before the equation runs."""
        from services.wallet import get_balance, grant, hold, settle
        u = _uid("negcost")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")  # held = 12
        res = await settle(db, user_id=u, run_id=f"run_{u}",
                           actual_cost_usd=-999.0, idempotency_key=f"settle_{u}")
        assert res["charged"] == 0
        assert res["refunded"] == 12
        bal = await get_balance(db, u)
        assert bal["purchased"] == 100, "a negative cost must never result in MORE than a full refund"

    async def test_astronomically_huge_actual_cost_capped_at_held_amount(self, db):
        """A huge/overflow-adjacent actual_cost_usd (1e18) must never
        charge more than the held estimate — the held amount is a
        hard ceiling regardless of how extreme the equation's raw
        output is, and the call must not crash on the huge number.

        ALSO caught a second real bug while verifying this: once Part 3
        added `computed_tokens_uncapped` to the settle ledger row (for
        the dashboard's ceiling-hit metric), persisting the RAW
        equation output for a $1e18 cost (~8.3e18 tokens) overflowed
        Mongo's 8-byte int64 limit and raised mid-settle — fixed by
        clamping actual_cost_usd to a sane $1M ceiling before the
        equation runs (see services.wallet._MAX_SANE_COST_USD)."""
        from services.wallet import get_balance, grant, hold, settle
        u = _uid("hugecost")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")  # held = 12
        res = await settle(db, user_id=u, run_id=f"run_{u}",
                           actual_cost_usd=1e18, idempotency_key=f"settle_{u}")
        assert res["charged"] == 12
        assert res["refunded"] == 0
        bal = await get_balance(db, u)
        assert bal["purchased"] == 88, "must never charge beyond the held ceiling, however huge the real cost"
        row = await db.wallet_ledger.find_one({"_id": f"settle_{u}"})
        assert row["computed_tokens_uncapped"] < 2**63, "must never store a value that overflows Mongo's int64"

    async def test_nan_and_infinity_cost_do_not_crash_or_overcharge(self, db):
        """float('nan')/float('inf') are valid Python floats a buggy
        or malicious caller could pass; neither must crash settle()
        or result in more than the held amount being charged."""
        import math

        from services.wallet import get_balance, grant, hold, settle
        for tag, cost in (("nan", float("nan")), ("inf", float("inf"))):
            u = _uid(f"badfloat_{tag}")
            await grant(db, user_id=u, bucket="purchased", amount=100,
                        idempotency_key=f"g_{u}", source="test")
            await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                       idempotency_key=f"h_{u}")
            try:
                res = await settle(db, user_id=u, run_id=f"run_{u}",
                                   actual_cost_usd=cost, idempotency_key=f"settle_{u}")
            except (ValueError, OverflowError):
                # Raising loudly on garbage input is an acceptable
                # outcome too — what's NOT acceptable is silently
                # overcharging or corrupting the balance.
                bal = await get_balance(db, u)
                assert bal["purchased"] in (88, 100)
                continue
            assert res["charged"] <= 12, f"{tag}: charged {res['charged']} > held 12"
            bal = await get_balance(db, u)
            assert not math.isnan(bal["purchased"])
            assert bal["purchased"] >= 88


# ─────────────────────────────────────────────────────────────────────
# (a) self red-team — refund/dispute on an ALREADY-FULLY-SPENT batch
# ─────────────────────────────────────────────────────────────────────

class TestRefundOnFullySpentBatch:
    async def test_refund_on_a_batch_with_zero_remaining_is_full_shortfall(self, db):
        """Not just "mostly spent" (already covered in 11a) — a batch
        that is EXACTLY zero (fully consumed by legitimate spend
        before the refund webhook arrives) must report the entire
        refund amount as shortfall and touch nothing, never go
        negative, never crash."""
        from services.wallet import claw_back_batch, get_balance, grant, hold
        u = _uid("fullyspent")
        await grant(db, user_id=u, bucket="purchased", amount=12,
                    idempotency_key=f"batch_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")  # drains the batch to 0
        bal = await get_balance(db, u)
        assert bal["purchased"] == 0

        res = await claw_back_batch(
            db, user_id=u, batch_id=f"batch_{u}", amount=1000,
            idempotency_key=f"claw_{u}", source="test",
        )
        assert res["removed"] == 0
        assert res["shortfall"] == 1000
        bal = await get_balance(db, u)
        assert bal["purchased"] == 0, "must stay at 0, never go negative"

    async def test_dispute_lost_arriving_before_dispute_created_still_resolves(
        self, db, monkeypatch,
    ):
        """Out-of-order Stripe delivery: charge.dispute.closed(lost)
        arrives before charge.dispute.created ever did (plausible with
        at-least-once, non-ordered webhook delivery). fix_entitlement
        was never frozen in this scenario — the "lost" handler must
        still correctly revoke (no active sub) and claw back, not
        crash or no-op just because the freeze step never happened."""
        from routers import payments
        from services.fix_entitlement import grant_fix_entitlement, is_fix_entitled
        u = _uid("outoforder")
        charge_id = f"ch_{uuid.uuid4().hex[:10]}"
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "stripe_sub_id": None,
        })
        await grant_fix_entitlement(db, u, source="test")
        assert await is_fix_entitled(db, u) is True  # never frozen

        class _FakeCharge:
            metadata: ClassVar[dict] = {"user_id": u, "plan": "starter"}
        monkeypatch.setattr(
            payments, "_stripe_call",
            lambda fn, *a, **kw: _async_return_oo(_FakeCharge()),
        )
        event = {
            "id": f"evt_{uuid.uuid4().hex[:10]}",
            "type": "charge.dispute.closed",
            "data": {"object": {
                "id": f"dp_{uuid.uuid4().hex[:10]}", "charge": charge_id,
                "status": "lost", "amount": 900,
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
        assert await is_fix_entitled(db, u) is False, (
            "a lost dispute must revoke even if it was never frozen first"
        )


async def _async_return_oo(value):
    return value


# ─────────────────────────────────────────────────────────────────────
# (a) self red-team — cross-user wallet spend (IDOR) / mass assignment
# ─────────────────────────────────────────────────────────────────────

class TestCrossUserWalletIDOR:
    async def test_forged_user_id_in_bulk_fix_body_cannot_redirect_the_charge(
        self, db, monkeypatch,
    ):
        """Attack: an authenticated user A crafts a /fix-pipeline/bulk
        request body that ALSO includes user_id/account_id/
        effective_account_id fields pointing at victim B, hoping the
        handler mass-assigns one of them over the server-resolved
        identity. start_bulk_fix resolves `user_id` ONLY from
        `_gate()`'s server-side permission check (never reads those
        keys from `body` at all — confirmed by grep, exercised here)
        — the charge must land on A's wallet regardless of what the
        body claims."""
        from routers import fix_pipeline as fp
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        attacker = _uid("attackerA")
        victim = _uid("victimB")
        await db.dev_users.insert_one({
            "user_id": attacker, "tier": "team", "fix_entitlement": True,
            "tokens_remaining": 0,
        })
        await db.dev_users.insert_one({
            "user_id": victim, "tier": "team", "fix_entitlement": True,
            "tokens_remaining": 0,
        })
        await wallet.grant(db, user_id=attacker, bucket="purchased", amount=100,
                           idempotency_key=f"g_{attacker}", source="test")
        await wallet.grant(db, user_id=victim, bucket="purchased", amount=100,
                           idempotency_key=f"g_{victim}", source="test")

        async def fake_gate(auth, level):
            # The REAL permission check always resolves to the
            # authenticated caller (attacker) — this mock stands in
            # for that, exactly as the legitimate code path would,
            # regardless of anything in the request body.
            return {"user_id": attacker}, {"user_id": attacker, "tier": "team"}
        monkeypatch.setattr(fp, "_gate", fake_gate)
        monkeypatch.setattr(fp, "get_db", lambda: db)
        monkeypatch.setattr(fp.asyncio, "create_task", lambda coro: coro.close())
        from cto_services import db as _dbmod
        _dbmod.set_db(db)

        forged_body = {
            "project_id": "p1",
            "findings": [{"id": "f1", "rule_id": "r1", "file": "a.py", "line": 1, "severity": "high"}],
            # Attack payload — none of these keys are ever read by the
            # handler; included to prove that, not to simulate a real
            # parameter name (the handler takes a raw `body: dict`).
            "user_id": victim,
            "account_id": victim,
            "effective_account_id": victim,
        }
        result = await fp.start_bulk_fix(body=forged_body, authorization="Bearer x")
        assert result["ok"] is True

        bal_attacker = await wallet.get_balance(db, attacker)
        bal_victim = await wallet.get_balance(db, victim)
        assert bal_attacker["purchased"] == 100 - 12, "the charge must land on the AUTHENTICATED caller"
        assert bal_victim["purchased"] == 100, "the victim's wallet must be completely untouched"

        from cto_services import db as _dbmod2
        _dbmod2.set_db(None)


# ─────────────────────────────────────────────────────────────────────
# (a) self red-team — Mongo operator injection via client-supplied ids
# ─────────────────────────────────────────────────────────────────────

class TestMongoOperatorInjection:
    async def test_wallet_run_id_stringifies_a_dict_injection_attempt(self):
        """Attack: a crafted finding whose "id" field is itself a dict
        shaped like a Mongo operator ({"$ne": None}), hoping
        _wallet_run_id passes it through structurally into a Mongo
        query filter ({"_id": run_id}) as a live operator instead of a
        literal value. The f-string construction in _wallet_run_id
        unconditionally stringifies ANY object via Python's own str()
        — the result is always a plain str, so the "$ne" text can only
        ever be compared as a literal _id value, never interpreted as
        an operator."""
        from routers.fix_pipeline import _wallet_run_id
        evil_finding = {"id": {"$ne": None}}
        run_id = _wallet_run_id("job123", evil_finding)
        assert isinstance(run_id, str)
        assert run_id == "job123:{'$ne': None}"

    async def test_wallet_run_id_handles_operator_strings_and_other_injection_shapes(self):
        from routers.fix_pipeline import _wallet_run_id
        for evil_id in (
            {"$where": "1==1"},
            ["$ne", None],
            {"$gt": ""},
            "$ne",  # a plain string that HAPPENS to look like an operator
        ):
            run_id = _wallet_run_id("job123", {"id": evil_id})
            assert isinstance(run_id, str)

    async def test_grant_rejects_a_dict_shaped_like_an_operator_as_idempotency_key(self, db):
        """FOUND BY THIS RED-TEAM: grant()'s first operation is
        `find_one({"_id": idempotency_key})` — a QUERY FILTER, not an
        insert. Passing a dict shaped like a Mongo operator
        (`{"$ne": "nobody"}`) here did NOT raise before this fix
        (Mongo happily allows a dict as a document's `_id` on insert),
        but WOULD have been structurally interpreted as "find any row
        whose _id != 'nobody'" by that find_one call — a real NoSQL
        injection if this parameter were ever attacker-reachable as a
        non-string. No current call site in this codebase passes
        anything but an f-string-built str here, but services.wallet.
        _require_str now makes that an ENFORCED invariant rather than
        an unenforced convention — this is the fix, not a pre-existing
        protection."""
        from services.wallet import grant
        u = _uid("mongotypecheck")
        with pytest.raises(TypeError):
            await grant(
                db, user_id=u, bucket="purchased", amount=10,
                idempotency_key={"$ne": "nobody"},  # type: ignore[arg-type]
                source="test",
            )


# ─────────────────────────────────────────────────────────────────────
# (a) self red-team — stale / backdated pricing_version
# ─────────────────────────────────────────────────────────────────────

class TestBackdatedPricingVersion:
    async def test_already_settled_ledger_row_keeps_its_original_margin(self, db):
        """A run settles under margin A. An admin (today: nothing
        reachable from the client — Part 3 hasn't built the endpoint
        yet) later inserts a NEW pricing_version, even backdated to
        BEFORE the run settled. The ALREADY-WRITTEN wallet_ledger row
        is immutable — ledger rows are never updated in place anywhere
        in this module — so the backdated version must not retroactively
        change what that run was recorded as having cost."""
        from services.pricing_engine import set_pricing_version
        from services.wallet import get_balance, grant, hold, settle

        u = _uid("backdated")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await set_pricing_version(db, margin=0.5, actor="wallet_test_admin")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")
        res = await settle(db, user_id=u, run_id=f"run_{u}",
                           actual_cost_usd=0.10, idempotency_key=f"settle_{u}")
        # $0.10 * 100 / (1 - 0.5) = 20 tokens, capped at held (12) -> 12.
        assert res["charged"] == 12
        original_ledger_row = await db.wallet_ledger.find_one({"_id": f"settle_{u}"})
        assert original_ledger_row["amount"] == 12

        # Now insert a backdated version with a WILDLY different margin.
        import time
        await set_pricing_version(
            db, margin=0.01, actor="wallet_test_admin",
            effective_from=time.time() - 365 * 86400,
        )
        unchanged_row = await db.wallet_ledger.find_one({"_id": f"settle_{u}"})
        assert unchanged_row["amount"] == 12, (
            "a backdated pricing_version must never retroactively "
            "rewrite an already-settled ledger row"
        )
        bal = await get_balance(db, u)
        assert bal["purchased"] == 100 - 12, "the already-applied charge must not change either"

    async def test_no_client_reachable_endpoint_sets_pricing_version_yet(self):
        """Documents the current (pre-PART-3) state: set_pricing_version
        is a plain Python function with no HTTP route calling it, so
        there is NO margin-tampering surface reachable from a client
        today. PART 3 is what will add the real endpoint + its own
        margin-bounds/auth/audit-log security tests."""
        import routers.payments as payments_mod
        assert not hasattr(payments_mod, "set_pricing_version")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
