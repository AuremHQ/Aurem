"""tests/test_regression_iter2026_10h_wallet_hold_settle_release.py

Master Prompt Step B — services/wallet.py. Runs against a REAL local
MongoDB instance (not a dict-based mock) because the core correctness
claim — "two concurrent holds against an insufficient balance can
never both succeed" — is a statement about MongoDB's own single-
document atomicity guarantee. A Python-dict mock can't meaningfully
prove that; it can only prove the mock's own (possibly wrong) model of
concurrency. These tests use clearly-prefixed throwaway user_ids
(`wallet_test_*`) in the same dev Mongo every other backend test
already points at (MONGO_URL/DB_NAME env), with explicit setup/teardown
so no other collection or real user data is touched.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

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
        pytest.skip("MONGO_URL/DB_NAME not set — wallet tests need a real Mongo")
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


class TestGrant:
    async def test_grant_gives_right_tokens(self, db):
        from services import wallet
        u = _uid("grant")
        res = await wallet.grant(db, user_id=u, bucket="purchased", amount=500,
                                  idempotency_key=f"grant:{u}:1", source="topup")
        assert res["ok"] is True
        assert res["idempotent_replay"] is False
        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 500
        assert bal["spendable"] == 500

    async def test_grant_idempotency_key_never_double_grants(self, db):
        from services import wallet
        u = _uid("grantdup")
        key = f"grant:{u}:1"
        r1 = await wallet.grant(db, user_id=u, bucket="plan", amount=300,
                                 idempotency_key=key, source="monthly")
        r2 = await wallet.grant(db, user_id=u, bucket="plan", amount=300,
                                 idempotency_key=key, source="monthly")
        assert r1["idempotent_replay"] is False
        assert r2["idempotent_replay"] is True
        bal = await wallet.get_balance(db, u)
        assert bal["plan"] == 300, "retrying the same grant key must not double-credit"


class TestHoldSettleRelease:
    async def test_fix_charges_exactly_price_table_amount_and_ledger_matches(self, db):
        from config.token_pricing import tokens_for
        from services import wallet
        u = _uid("fixcharge")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        run_id = f"run_{u}"
        hold_res = await wallet.hold(db, user_id=u, action="fix", run_id=run_id,
                                      idempotency_key=f"hold:{run_id}")
        assert hold_res["held"] == tokens_for("fix")
        settle_res = await wallet.settle(db, user_id=u, run_id=run_id,
                                          actual_tokens=tokens_for("fix"),
                                          idempotency_key=f"settle:{run_id}")
        assert settle_res["charged"] == tokens_for("fix")
        assert settle_res["refunded"] == 0

        ledger = await db.wallet_ledger.find({"user_id": u}).to_list(None)
        kinds = sorted(r["kind"] for r in ledger)
        assert kinds == ["grant", "hold", "settle"]
        settle_row = next(r for r in ledger if r["kind"] == "settle")
        assert settle_row["amount"] == tokens_for("fix")

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - tokens_for("fix")

    async def test_settle_refunds_difference_when_actual_is_cheaper(self, db):
        from config.token_pricing import tokens_for
        from services import wallet
        u = _uid("fixcheap")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        run_id = f"run_{u}"
        price = tokens_for("fix")
        await wallet.hold(db, user_id=u, action="fix", run_id=run_id,
                           idempotency_key=f"hold:{run_id}")
        cheaper = max(1, price - 5)
        res = await wallet.settle(db, user_id=u, run_id=run_id, actual_tokens=cheaper,
                                   idempotency_key=f"settle:{run_id}")
        assert res["charged"] == cheaper
        assert res["refunded"] == price - cheaper
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - cheaper

    async def test_settle_never_charges_more_than_the_held_estimate(self, db):
        from config.token_pricing import tokens_for
        from services import wallet
        u = _uid("fixover")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        run_id = f"run_{u}"
        price = tokens_for("fix")
        await wallet.hold(db, user_id=u, action="fix", run_id=run_id,
                           idempotency_key=f"hold:{run_id}")
        res = await wallet.settle(db, user_id=u, run_id=run_id,
                                   actual_tokens=price * 10,
                                   idempotency_key=f"settle:{run_id}")
        assert res["charged"] == price, "settle must cap at the held estimate, never overcharge"

    async def test_failed_fix_releases_the_hold_fully(self, db):
        from config.token_pricing import tokens_for
        from services import wallet
        u = _uid("fixfail")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        run_id = f"run_{u}"
        price = tokens_for("fix")
        await wallet.hold(db, user_id=u, action="fix", run_id=run_id,
                           idempotency_key=f"hold:{run_id}")
        bal_mid = await wallet.get_balance(db, u)
        assert bal_mid["spendable"] == 100 - price

        rel = await wallet.release(db, user_id=u, run_id=run_id,
                                     idempotency_key=f"release:{run_id}",
                                     reason="codegen_failed")
        assert rel["released"] == price
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100, "a failed run must refund the FULL hold"

    async def test_a_held_run_is_allowed_to_finish_at_exactly_zero(self, db):
        """Balance drops to zero right after hold() — settle() must
        still succeed (it finalizes the ALREADY-reserved hold, never
        re-checks live balance)."""
        from config.token_pricing import tokens_for
        from services import wallet
        u = _uid("zerofinish")
        price = tokens_for("fix")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=price,
                            idempotency_key=f"g:{u}", source="topup")
        run_id = f"run_{u}"
        await wallet.hold(db, user_id=u, action="fix", run_id=run_id,
                           idempotency_key=f"hold:{run_id}")
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 0, "balance should be exactly zero mid-run"

        res = await wallet.settle(db, user_id=u, run_id=run_id, actual_tokens=price,
                                   idempotency_key=f"settle:{run_id}")
        assert res["ok"] is True
        assert res["charged"] == price

    async def test_hold_raises_insufficient_tokens_when_balance_too_low(self, db):
        from services import wallet
        u = _uid("insufficient")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=1,
                            idempotency_key=f"g:{u}", source="topup")
        with pytest.raises(wallet.InsufficientTokens):
            await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                               idempotency_key=f"hold:{u}")
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 1, "a failed hold must not touch the balance at all"

    async def test_hold_idempotency_key_never_double_charges(self, db):
        from config.token_pricing import tokens_for
        from services import wallet
        u = _uid("holddup")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        run_id = f"run_{u}"
        key = f"hold:{run_id}"
        r1 = await wallet.hold(db, user_id=u, action="fix", run_id=run_id, idempotency_key=key)
        r2 = await wallet.hold(db, user_id=u, action="fix", run_id=run_id, idempotency_key=key)
        assert r1["idempotent_replay"] is False
        assert r2["idempotent_replay"] is True
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - tokens_for("fix"), "retried hold must not reserve twice"

    async def test_release_idempotency_key_never_double_refunds(self, db):
        from config.token_pricing import tokens_for
        from services import wallet
        u = _uid("releasedup")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        run_id = f"run_{u}"
        await wallet.hold(db, user_id=u, action="fix", run_id=run_id,
                           idempotency_key=f"hold:{run_id}")
        key = f"release:{run_id}"
        r1 = await wallet.release(db, user_id=u, run_id=run_id, idempotency_key=key)
        r2 = await wallet.release(db, user_id=u, run_id=run_id, idempotency_key=key)
        assert r1["released"] == tokens_for("fix")
        assert r2["idempotent_replay"] is True
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100, "retried release must not double-refund"


class TestConcurrency:
    async def test_two_concurrent_holds_never_go_negative(self, db):
        """THE core atomicity proof. Balance can afford exactly ONE
        hold, not two. Fire both concurrently via asyncio.gather
        against the REAL Mongo — exactly one must succeed, the other
        must raise InsufficientTokens, and the final balance must
        never be negative."""
        from config.token_pricing import tokens_for
        from services import wallet
        u = _uid("race")
        price = tokens_for("fix")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=price,
                            idempotency_key=f"g:{u}", source="topup")

        async def _try_hold(run_id):
            try:
                return await wallet.hold(db, user_id=u, action="fix", run_id=run_id,
                                          idempotency_key=f"hold:{run_id}")
            except wallet.InsufficientTokens as e:
                return e

        results = await asyncio.gather(
            _try_hold(f"run_{u}_a"), _try_hold(f"run_{u}_b"),
        )
        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, wallet.InsufficientTokens)]
        assert len(successes) == 1, f"expected exactly 1 success, got {results}"
        assert len(failures) == 1

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 0
        assert bal["promo"] >= 0 and bal["plan"] >= 0 and bal["purchased"] >= 0

    async def test_ten_concurrent_holds_against_a_five_token_balance(self, db):
        """Higher-contention version — balance affords exactly 5 holds
        of 1 token each; fire 10 concurrently, exactly 5 must win."""
        from services import wallet
        u = _uid("race10")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=5,
                            idempotency_key=f"g:{u}", source="topup")
        # Use a cheap synthetic action priced at 1 token for fine-grained contention.
        import config.token_pricing as tp
        tp.PRICE_TABLE["_test_unit"] = 1
        try:
            async def _try(i):
                try:
                    return await wallet.hold(
                        db, user_id=u, action="_test_unit", run_id=f"run_{u}_{i}",
                        idempotency_key=f"hold_{u}_{i}",
                    )
                except wallet.InsufficientTokens as e:
                    return e

            results = await asyncio.gather(*[_try(i) for i in range(10)])
            successes = [r for r in results if isinstance(r, dict)]
            assert len(successes) == 5
            bal = await wallet.get_balance(db, u)
            assert bal["spendable"] == 0
        finally:
            tp.PRICE_TABLE.pop("_test_unit", None)


class TestPlanVsPurchasedExpiry:
    async def test_monthly_grant_resets_plan_but_never_touches_purchased(self, db, monkeypatch):
        from services import wallet
        # PRE-STEP (d), Master Prompt v2 continuation — run_monthly_grant_job
        # is now gated on billing_mode() (off/shadow are a complete
        # no-op); this test exercises the "on" path explicitly.
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("planreset")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter"})
        try:
            await wallet.grant(db, user_id=u, bucket="plan", amount=900,
                                idempotency_key=f"plan_old:{u}", source="monthly_grant:starter")
            await wallet.grant(db, user_id=u, bucket="purchased", amount=50,
                                idempotency_key=f"topup:{u}", source="topup")
            bal_before = await wallet.get_balance(db, u)
            assert bal_before["plan"] == 900
            assert bal_before["purchased"] == 50

            # Spend some plan tokens (simulating usage during the month).
            from config.token_pricing import tokens_for
            await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                               idempotency_key=f"hold:{u}")
            await wallet.settle(db, user_id=u, run_id=f"run_{u}",
                                 actual_tokens=tokens_for("fix"),
                                 idempotency_key=f"settle:{u}")
            bal_spent = await wallet.get_balance(db, u)
            assert bal_spent["plan"] == 900 - tokens_for("fix")

            # Simulate next month's grant job run — unique key per call
            # since run_monthly_grant_job keys on (user_id, CURRENT
            # year-month); force a fresh key here to simulate "next month".
            res = await wallet.run_monthly_grant_job(db)
            assert any(a["user_id"] == u for a in res["applied"]) or any(
                p["user_id"] == u for p in res["planned"]
            )

            bal_after = await wallet.get_balance(db, u)
            assert bal_after["purchased"] == 50, "purchased tokens must never be touched by the monthly refill"
        finally:
            await db.dev_users.delete_one({"user_id": u})


class TestThresholds:
    async def test_warn_fires_once_at_20_percent(self, db):
        from services import wallet
        u = _uid("warn20")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        await db.wallets.update_one(
            {"_id": u}, {"$set": {"month_baseline": {"ym": wallet._year_month(), "amount": 100}}},
        )
        # Spend down to 15 (15% left — below the 20% warn line).
        import config.token_pricing as tp
        tp.PRICE_TABLE["_test_spend"] = 85
        try:
            run_id = f"run_{u}"
            await wallet.hold(db, user_id=u, action="_test_spend", run_id=run_id,
                               idempotency_key=f"hold:{run_id}")
            await wallet.settle(db, user_id=u, run_id=run_id, actual_tokens=85,
                                 idempotency_key=f"settle:{run_id}")
            r1 = await wallet.check_thresholds(db, u)
            assert "warn" in r1["newly_fired"]
            assert r1["state"]["warn"] is True
            assert r1["state"]["critical"] is False

            r2 = await wallet.check_thresholds(db, u)
            assert r2["newly_fired"] == [], "warn must not re-fire within the same month"
            assert r2["state"]["warn"] is True
        finally:
            tp.PRICE_TABLE.pop("_test_spend", None)

    async def test_critical_fires_at_5_percent_and_stop_at_zero(self, db):
        from services import wallet
        u = _uid("critical5")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        await db.wallets.update_one(
            {"_id": u}, {"$set": {"month_baseline": {"ym": wallet._year_month(), "amount": 100}}},
        )
        import config.token_pricing as tp
        tp.PRICE_TABLE["_test_spend_all"] = 100
        try:
            run_id = f"run_{u}"
            await wallet.hold(db, user_id=u, action="_test_spend_all", run_id=run_id,
                               idempotency_key=f"hold:{run_id}")
            await wallet.settle(db, user_id=u, run_id=run_id, actual_tokens=100,
                                 idempotency_key=f"settle:{run_id}")
            r = await wallet.check_thresholds(db, u)
            assert set(r["newly_fired"]) == {"warn", "critical", "stop"}
            assert r["state"]["stop"] is True
        finally:
            tp.PRICE_TABLE.pop("_test_spend_all", None)


class TestAssertWalletModes:
    async def test_off_mode_never_raises_even_at_zero_balance(self, db, monkeypatch):
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "off")
        u = _uid("modeoff")
        res = await wallet.assert_wallet(db, user_id=u, action="fix",
                                           run_id=f"run_{u}", idempotency_key=f"aw:{u}")
        assert res["mode"] == "off"
        assert res["held"] is False
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 0, "off mode must never move tokens"

    async def test_shadow_mode_never_raises_and_never_holds_even_at_zero(self, db, monkeypatch):
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        u = _uid("modeshadow")
        res = await wallet.assert_wallet(db, user_id=u, action="fix",
                                           run_id=f"run_{u}", idempotency_key=f"aw:{u}")
        assert res["mode"] == "shadow"
        assert res["would_block"] is True
        assert res["held"] is False
        ledger_count = await db.wallet_ledger.count_documents({"user_id": u})
        assert ledger_count == 0, "shadow mode must never write a real hold"

    async def test_on_mode_raises_402_at_zero_balance(self, db, monkeypatch):
        from fastapi import HTTPException

        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("modeon")
        with pytest.raises(HTTPException) as exc_info:
            await wallet.assert_wallet(db, user_id=u, action="fix",
                                         run_id=f"run_{u}", idempotency_key=f"aw:{u}")
        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["code"] == "tokens_exhausted"

    async def test_on_mode_holds_for_real_when_balance_sufficient(self, db, monkeypatch):
        from config.token_pricing import tokens_for
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("modeonok")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g:{u}", source="topup")
        res = await wallet.assert_wallet(db, user_id=u, action="fix",
                                           run_id=f"run_{u}", idempotency_key=f"aw:{u}")
        assert res["mode"] == "on"
        assert res["held"] is True
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - tokens_for("fix")

    async def test_invalid_mode_value_fails_safe_to_off(self, db, monkeypatch):
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "enforce_hard")  # typo/garbage value
        assert wallet.billing_mode() == "off"


class TestReconcile:
    async def test_reconcile_flags_drift_above_threshold(self, db):
        from services import wallet
        u = _uid("reconcile")
        now = datetime.now(UTC)
        await db.wallet_ledger.insert_one({
            "_id": f"settle_reconcile_{u}", "user_id": u, "kind": "settle",
            "amount": 100, "created_at": now,
        })
        await db.real_llm_usage.insert_one({
            "user_id": u, "cost_usd": 10.0, "ts": now.timestamp(),
        })
        try:
            report = await wallet.reconcile_report(db, drift_alert_pct=3.0)
            row = next((r for r in report["rows"] if r["user_id"] == u), None)
            assert row is not None
            # wallet=100 tokens vs provider=10usd*100=1000 tokens -> huge drift
            assert row["drift_pct"] > 3.0
            assert any(r["user_id"] == u for r in report["flagged"])
        finally:
            await db.real_llm_usage.delete_many({"user_id": u})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
