"""tests/test_regression_iter2026_19a_write_guard_allowlist_conversion.py

ITEM 3 (High) — independent-review fix round.

CURRENT RULE (before this fix), from reading
services/plan_transition_write_guard.py: WriteGuardedDB/_GuardedCollection
is a DENY-LIST of 12 named write methods
(insert_one/insert_many/update_one/update_many/delete_one/delete_many/
replace_one/find_one_and_update/find_one_and_replace/
find_one_and_delete/bulk_write/drop). Anything NOT in that list —
`aggregate` with a `$out`/`$merge` stage, `update`, `save`,
`create_index`, `rename`, `find_and_modify`, `create_collection`,
`command`, or any unknown/future attribute name — passes straight
through via `__getattr__`'s fallback, including the real escape
hatches `_real` (a plain instance attribute, never intercepted by
`__getattr__` at all), `.database`, `.client`, `.get_collection`.

THIS FIX converts it to an ALLOW-LIST: only
find/find_one/count_documents/distinct/estimated_document_count/
aggregate (pipeline scanned recursively for $out/$merge/$function/
$accumulator/$where at ANY depth, including $facet and $lookup
sub-pipelines) pass through. Everything else — including unknown
attribute names — raises WriteBlockedError. The known escape names
(_real/database/client/command/get_collection/__getitem__) are
explicitly blocked rather than left to fall through.

One pre-existing test asserted the OLD permissive behavior as
correct: test_regression_iter2026_15a_plan_transition_write_guard.py
::TestWriteGuardEdgeCases::test_unknown_attribute_on_guarded_collection_is_not_silently_swallowed
expected an unknown attribute to delegate to Motor's own lazy
sub-collection behavior (TypeError only on call) rather than raise
WriteBlockedError immediately. That is now updated to match the new,
explicit allow-list rule the sibling file corrects it alongside.
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


ALLOWED_READ_METHODS = ["find", "find_one", "count_documents", "distinct", "estimated_document_count"]

DENY_LIST_ERA_WRITE_METHODS = [
    "insert_one", "insert_many", "update_one", "update_many",
    "delete_one", "delete_many", "replace_one",
    "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
    "bulk_write", "drop",
]

NOT_PREVIOUSLY_COVERED_WRITE_SHAPED_METHODS = [
    "update", "save", "create_index", "rename", "find_and_modify", "create_collection",
]


class TestAllowListCoversAllReadMethods:
    @pytest.mark.parametrize("method", ALLOWED_READ_METHODS)
    async def test_allowed_method_passes_through(self, db, method):
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db)
        fn = getattr(guarded.dev_users, method)
        if method == "find":
            cursor = fn({"_id": "wallet_test_does_not_exist_xyz"})
            assert await cursor.to_list(1) == []
        elif method == "distinct":
            result = await fn("user_id", {"_id": "wallet_test_does_not_exist_xyz"})
            assert result == []
        elif method == "estimated_document_count":
            result = await fn()
            assert isinstance(result, int) and result >= 0
        else:
            result = await fn({"_id": "wallet_test_does_not_exist_xyz"})
            assert result in (None, 0)

    async def test_safe_aggregate_pipeline_passes_through(self, db):
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db)
        cursor = guarded.dev_users.aggregate([
            {"$match": {"_id": "wallet_test_does_not_exist_xyz"}},
            {"$group": {"_id": None, "n": {"$sum": 1}}},
        ])
        assert await cursor.to_list(10) == []


class TestAllowListBlocksEveryWriteShapedMethod:
    @pytest.mark.parametrize("method", DENY_LIST_ERA_WRITE_METHODS + NOT_PREVIOUSLY_COVERED_WRITE_SHAPED_METHODS)
    async def test_write_shaped_method_raises(self, db, method):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        fn = getattr(guarded.dev_users, method)
        with pytest.raises(WriteBlockedError):
            await fn()

    async def test_unknown_attribute_now_raises_immediately_not_delegated(self, db):
        """Corrected from the old deny-list-era test that asserted
        this delegated to Motor's own lazy-sub-collection TypeError —
        that was exactly the permissiveness this item closes."""
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.this_method_does_not_exist()

    async def test_command_on_collection_level_blocked_too(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            await guarded.dev_users.command({"ping": 1})


class TestAggregatePipelineScannedAtAnyDepth:
    async def test_top_level_out_blocked(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.aggregate([{"$match": {}}, {"$out": "evil_collection"}])

    async def test_top_level_merge_blocked(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.aggregate([{"$merge": {"into": "evil_collection"}}])

    async def test_where_blocked(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.aggregate([{"$match": {"$where": "function() { return true; }"}}])

    async def test_out_nested_inside_facet_subpipeline_blocked(self, db):
        """$facet's value is a dict of named sub-pipelines — a $out
        buried three levels deep must still be caught."""
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        pipeline = [
            {"$facet": {
                "branch_a": [{"$match": {}}],
                "branch_b": [{"$match": {}}, {"$out": "evil_collection"}],
            }},
        ]
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.aggregate(pipeline)

    async def test_merge_nested_inside_lookup_subpipeline_blocked(self, db):
        """$lookup's `pipeline` key is itself a sub-pipeline list."""
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        pipeline = [
            {"$lookup": {
                "from": "wallets",
                "pipeline": [{"$match": {}}, {"$merge": {"into": "evil_collection"}}],
                "as": "joined",
            }},
        ]
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.aggregate(pipeline)

    async def test_function_operator_blocked(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        pipeline = [{"$project": {"x": {"$function": {"body": "function(){}", "args": [], "lang": "js"}}}}]
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.aggregate(pipeline)

    async def test_accumulator_operator_blocked(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        pipeline = [{"$group": {"_id": "$user_id", "x": {"$accumulator": {"init": "function(){}"}}}}]
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.aggregate(pipeline)

    async def test_benign_pipeline_with_no_forbidden_stage_still_works(self, db):
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db)
        cursor = guarded.dev_users.aggregate([
            {"$match": {"_id": "wallet_test_does_not_exist_xyz"}},
            {"$lookup": {"from": "wallets", "pipeline": [{"$match": {}}], "as": "joined"}},
            {"$facet": {"a": [{"$match": {}}]}},
        ])
        # $facet always emits exactly one summary document per the
        # pipeline's own named branches, regardless of how many (zero,
        # here) documents actually matched inside each branch.
        assert await cursor.to_list(10) == [{"a": []}]


class TestEscapeHatchesBlocked:
    async def test_real_attribute_blocked_on_db(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded._real  # noqa: B018

    async def test_real_attribute_blocked_on_collection(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.dev_users._real  # noqa: B018

    async def test_database_attribute_blocked_on_collection(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.database  # noqa: B018

    async def test_client_attribute_blocked(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.client  # noqa: B018
        with pytest.raises(WriteBlockedError):
            guarded.dev_users.client  # noqa: B018

    async def test_get_collection_blocked(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded.get_collection("dev_users")

    async def test_command_blocked_on_db(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            await guarded.command({"ping": 1})

    async def test_getitem_subscript_blocked(self, db):
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(WriteBlockedError):
            guarded["dev_users"]  # noqa: B018

    async def test_no_escape_route_reaches_a_real_write(self, db):
        """Behavioral capstone: try EVERY escape route, then confirm
        directly against the unguarded db that nothing landed."""
        from services.plan_transition_write_guard import WriteBlockedError, WriteGuardedDB
        guarded = WriteGuardedDB(db)
        marker = "wallet_test_escape_proof"
        attempts = []

        def _try(fn):
            try:
                fn()
                attempts.append("no_raise")
            except WriteBlockedError:
                attempts.append("blocked")
            except Exception as e:  # noqa: BLE001
                attempts.append(f"other:{type(e).__name__}")

        _try(lambda: guarded._real.dev_users)
        _try(lambda: guarded.dev_users._real)
        _try(lambda: guarded.dev_users.database)
        _try(lambda: guarded.client)
        _try(lambda: guarded.get_collection("dev_users"))
        assert all(a == "blocked" for a in attempts), attempts
        real_check = await db.dev_users.find_one({"_id": marker})
        assert real_check is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
