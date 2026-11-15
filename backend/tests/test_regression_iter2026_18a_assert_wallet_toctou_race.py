"""tests/test_regression_iter2026_18a_assert_wallet_toctou_race.py

ITEM 2 (BLOCKER, High) — independent-review fix round.

ROOT CAUSE of the "intermittent" failure in
test_regression_iter2026_10y_bulk_fix_wallet_wiring.py::
test_two_concurrent_bulk_starts_never_push_balance_negative: NOT a
test flake. `services/wallet.py::assert_wallet` (mode "on") does a
non-atomic, two-step check: (1) `bal = await get_balance(...)`,
compute `would_block`, and only raise a clean `HTTPException(402)`
if THAT snapshot looks insufficient; (2) if the snapshot looked fine,
`await hold(...)` — which does its OWN atomic compare-and-swap
against the REAL current balance and raises `InsufficientTokens`
(services/wallet.py:481) if that atomic check fails.

Under concurrency, a caller's step-(1) snapshot can be stale by the
time step-(2) actually runs (another caller's hold landed in
between) — `assert_wallet` then lets a RAW `InsufficientTokens`
escape, instead of the clean `HTTPException(402)` every caller is
contracted to expect. Reproduced live via a stress script (100 runs
clean, then an uncaught `InsufficientTokens` traceback on the very
first trial under injected CPU load) — confirming the race is real,
not timing-sensitive noise, and reproduced here DETERMINISTICALLY
with a barrier instead of relying on scheduler luck.

Consequences of the escaped exception in routers/fix_pipeline.py::
start_bulk_fix (one of 6 call sites of assert_wallet — this is a
shared risk, not a fix_pipeline-only bug):
  - the `except HTTPException:` block that releases already-held
    findings and closes the job as failed NEVER RUNS — any finding
    held earlier in the same loop leaks (never settled, never
    released) until external reconciliation, if any exists.
  - the raw exception propagates uncaught past the router.

THE FIX: close the TOCTOU gap at its single source — `assert_wallet`
now catches `InsufficientTokens` from the real `hold()` call (not
just its own optimistic pre-check) and converts it to the same
`HTTPException(402)` contract every caller already expects. This
fixes all 6 call sites at once, not just fix_pipeline.py.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)

FIX_PRICE = 12


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


class TestAssertWalletNeverLeaksRawInsufficientTokens:
    async def test_deterministic_stale_precheck_race_still_raises_clean_402(self, db, monkeypatch):
        """Barrier-forced repro, no timing luck: caller A's optimistic
        pre-check reads the balance BEFORE caller B's real hold lands,
        but A's real hold() call doesn't run until AFTER B's hold has
        consumed the balance — the exact TOCTOU window. A must still
        get a clean HTTPException(402), never a raw InsufficientTokens."""
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = _uid("toctou")
        await wallet.grant(
            db, user_id=u, bucket="purchased", amount=FIX_PRICE,
            idempotency_key=f"grant:{u}", source="test",
        )

        real_get_balance = wallet.get_balance
        b_may_proceed = asyncio.Event()
        b_done = asyncio.Event()
        call_count = {"n": 0}

        async def _racy_get_balance(db_, user_id_):
            call_count["n"] += 1
            is_a_first_call = call_count["n"] == 1
            bal = await real_get_balance(db_, user_id_)
            if is_a_first_call:
                # Caller A has now read a balance that looks sufficient
                # (spendable == FIX_PRICE). Let caller B run to
                # completion (it will consume the balance for real)
                # before A's assert_wallet proceeds to its own real
                # hold() call below.
                b_may_proceed.set()
                await b_done.wait()
            return bal

        monkeypatch.setattr(wallet, "get_balance", _racy_get_balance)

        async def _caller_a():
            run_id = f"run_a_{u}"
            try:
                await wallet.assert_wallet(
                    db, user_id=u, action="fix", run_id=run_id,
                    idempotency_key=f"hold:{run_id}",
                )
                return "held"
            except HTTPException as e:
                return f"http_{e.status_code}"
            # Deliberately NOT catching wallet.InsufficientTokens — if
            # it escapes, the test fails with that raw exception,
            # exactly reproducing the original uncaught-exception bug.

        async def _caller_b():
            await b_may_proceed.wait()
            run_id = f"run_b_{u}"
            try:
                await wallet.assert_wallet(
                    db, user_id=u, action="fix", run_id=run_id,
                    idempotency_key=f"hold:{run_id}",
                )
                return "held"
            finally:
                b_done.set()

        result_a, result_b = await asyncio.gather(_caller_a(), _caller_b())

        assert result_b == "held", "B must win the real atomic hold"
        assert result_a == "http_402", (
            f"A's stale pre-check must still resolve to a clean 402, "
            f"not a raw InsufficientTokens escaping assert_wallet — got {result_a!r}"
        )

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 0
        assert bal["spendable"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
