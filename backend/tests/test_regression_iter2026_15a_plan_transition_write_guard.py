"""tests/test_regression_iter2026_15a_plan_transition_write_guard.py

Master Prompt v2 PART 6.1 — the write-guard wrapper that
plan_transition_dry_run.py is REQUIRED to use for all DB access. Any
write-shaped Motor call on a guarded collection raises, proving the
dry run cannot mutate real state even if a future bug tried to.

TDD: written and run RED before services/write_guard.py exists.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    yield database
    client.close()


WRITE_METHODS = [
    "insert_one", "insert_many", "update_one", "update_many",
    "delete_one", "delete_many", "replace_one",
    "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
    "bulk_write", "drop",
]


class TestWriteGuardedDbBlocksAllWrites:
    @pytest.mark.parametrize("method", WRITE_METHODS)
    async def test_every_write_method_raises(self, db, method):
        from services.plan_transition_write_guard import (
            WriteBlockedError,
            WriteGuardedDB,
        )
        guarded = WriteGuardedDB(db)
        fn = getattr(guarded.dev_users, method)
        with pytest.raises(WriteBlockedError):
            if method in ("insert_many", "bulk_write"):
                await fn([{"_id": "x"}])
            elif method == "drop":
                await fn()
            else:
                await fn({"_id": "x"}, {"$set": {"y": 1}}) if "update" in method or "replace" in method else await fn({"_id": "x"})

    async def test_read_methods_pass_through_unaffected(self, db):
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db)
        # find_one on a nonexistent doc should just return None, not raise.
        result = await guarded.dev_users.find_one({"_id": "wallet_test_does_not_exist_xyz"})
        assert result is None

    async def test_aggregate_and_count_documents_pass_through(self, db):
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db)
        count = await guarded.dev_users.count_documents({"_id": "wallet_test_does_not_exist_xyz"})
        assert count == 0
        cursor = guarded.dev_users.aggregate([{"$match": {"_id": "wallet_test_does_not_exist_xyz"}}])
        rows = await cursor.to_list(10)
        assert rows == []

    async def test_a_real_write_never_actually_reaches_mongo(self, db):
        """Behavioral, not just structural: attempt a write through
        the guard, then check directly against the UNGUARDED db that
        nothing landed."""
        from services.plan_transition_write_guard import (
            WriteBlockedError,
            WriteGuardedDB,
        )
        guarded = WriteGuardedDB(db)
        marker_id = "wallet_test_writeguard_proof"
        with pytest.raises(WriteBlockedError):
            await guarded.dev_users.insert_one({"_id": marker_id, "user_id": marker_id})
        real_check = await db.dev_users.find_one({"_id": marker_id})
        assert real_check is None
        await db.dev_users.delete_many({"_id": marker_id})  # cleanup, belt-and-braces


class TestWriteGuardAllowlist:
    async def test_an_allowlisted_collection_permits_writes(self, db):
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db, allow_write_to={"wallet_test_scratch_collection"})
        res = await guarded.wallet_test_scratch_collection.insert_one({"_id": "wallet_test_allow_1", "v": 1})
        assert res.inserted_id == "wallet_test_allow_1"
        await db.wallet_test_scratch_collection.delete_many({})

    async def test_a_non_allowlisted_collection_still_blocks(self, db):
        from services.plan_transition_write_guard import (
            WriteBlockedError,
            WriteGuardedDB,
        )
        guarded = WriteGuardedDB(db, allow_write_to={"wallet_test_scratch_collection"})
        with pytest.raises(WriteBlockedError):
            await guarded.dev_users.insert_one({"_id": "wallet_test_blocked"})


class TestWriteGuardEdgeCases:
    async def test_unknown_attribute_on_guarded_collection_is_blocked_immediately(self, db):
        """2026-10-25 (ITEM 3, independent review) — corrected from a
        deny-list-era expectation that an unknown name delegated to
        Motor's own lazy sub-collection behavior (TypeError only on
        call). That WAS the gap: any method this guard had never
        heard of (a future Motor API, `update`, `save`, ...) fell
        through untouched. The guard is now an ALLOW-LIST — an
        unrecognised name is blocked immediately, on the attribute
        access itself, not merely "eventually not callable"."""
        from services.plan_transition_write_guard import (
            WriteBlockedError,
            WriteGuardedDB,
        )
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.this_method_does_not_exist()

    async def test_guard_works_for_any_collection_name_including_unseen_ones(self, db):
        from services.plan_transition_write_guard import (
            WriteBlockedError,
            WriteGuardedDB,
        )
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            await guarded.some_collection_that_has_never_been_touched_before.insert_one({})
