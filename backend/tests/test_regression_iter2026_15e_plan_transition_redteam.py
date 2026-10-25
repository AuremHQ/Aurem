"""tests/test_regression_iter2026_15e_plan_transition_redteam.py

Master Prompt v2 PART 6.4 — red-team coverage not already exercised
by 15a-15d:
  - try to make the dry run write (15a already proves the guard
    blocks every write method; this file adds the end-to-end proof
    through run_dry_run/classify_user themselves, not just the
    guard in isolation)
  - oversized result (many users) does not blow up memory/time
    unreasonably
  - a race between the dry run and a REAL concurrent payment webhook
    event (wallet.grant / fix_entitlement) never corrupts either side
    — the dry run's snapshot-read-then-classify is read-only, so a
    concurrent real write is simply a benign race (the dry run might
    see before-or-after state, never a torn write), proven here
    rather than assumed
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
    from cto_services import db as _dbmod
    _dbmod.set_db(database)
    yield database
    _dbmod.set_db(None)
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})


class TestDryRunNeverWritesEndToEnd:
    async def test_run_dry_run_over_real_seeded_users_leaves_them_byte_identical(self, db):
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        ids = []
        for i in range(5):
            u = _uid(f"e2e{i}")
            await db.dev_users.insert_one({"user_id": u, "tier": "free", "email": f"{u}@example.com"})
            ids.append(u)
        before = {u: await db.dev_users.find_one({"user_id": u}) for u in ids}
        guarded = WriteGuardedDB(db)
        await run_dry_run(guarded, user_filter={"user_id": {"$in": ids}})
        after = {u: await db.dev_users.find_one({"user_id": u}) for u in ids}
        assert before == after


class TestOversizedResult:
    async def test_200_users_classified_without_excessive_time_or_crashing(self, db):
        import time as _time

        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        ids = []
        docs = []
        for i in range(200):
            u = _uid(f"bulk{i}")
            docs.append({"user_id": u, "tier": "free", "email": f"{u}@example.com"})
            ids.append(u)
        await db.dev_users.insert_many(docs)
        guarded = WriteGuardedDB(db)
        t0 = _time.monotonic()
        report = await run_dry_run(guarded, user_filter={"user_id": {"$in": ids}})
        elapsed = _time.monotonic() - t0
        assert len(report["rows"]) == 200
        assert elapsed < 60, f"200-user dry run took {elapsed:.1f}s — investigate before scaling to a real user base"


class TestRaceAgainstALivePaymentEvent:
    async def test_dry_run_survives_a_concurrent_real_wallet_grant(self, db):
        """The dry run's own reads are read-only; a REAL concurrent
        write from an unrelated live payment path (services.wallet.grant)
        must never cause the dry run to crash, corrupt its own output,
        or itself attempt a write in response."""
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        from services.wallet import grant

        u = _uid("race")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter", "stripe_sub_id": "sub_race",
                                        "fix_entitlement": True, "email": f"{u}@example.com"})
        guarded = WriteGuardedDB(db)

        async def _concurrent_real_payment():
            await asyncio.sleep(0.01)
            await grant(db, user_id=u, bucket="plan", amount=900,
                        idempotency_key=f"race_grant_{u}", source="test")

        dry_run_task = asyncio.create_task(run_dry_run(guarded, user_filter={"user_id": u}))
        payment_task = asyncio.create_task(_concurrent_real_payment())
        report, _ = await asyncio.gather(dry_run_task, payment_task)

        assert len(report["rows"]) == 1
        # Whichever side of the race the dry run saw, it must have
        # produced ONE well-formed classification — never a partial
        # or corrupted row.
        row = report["rows"][0]
        assert row["user_id"] == u
        assert row["segment"] is not None
        # The real payment's own write landed correctly regardless.
        wallet_doc = await db.wallets.find_one({"_id": u})
        assert wallet_doc is not None
        assert wallet_doc["buckets"]["plan"] == 900
