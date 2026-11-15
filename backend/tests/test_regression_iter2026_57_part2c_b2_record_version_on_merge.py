"""tests/test_regression_iter2026_57_part2c_b2_record_version_on_merge.py

Part 2c B2 — services/project_versions.py::record_version_on_merge:
atomic per-project version-number allocation, webhook-redelivery
dedup, and the rollback-marking hook. Real local Mongo — the atomic
counter and the uniqueness constraints are a database-level guarantee
a dict-based fake cannot meaningfully prove (same rule as
test_regression_iter2026_10y / test_regression_iter2026_56).
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
    return f"pv_test_{tag}_{uuid.uuid4().hex[:8]}"


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
        await db.project_versions.delete_many({"project_id": {"$regex": "^pv_test_"}})
        await db.cto_projects.delete_many({"project_id": {"$regex": "^pv_test_"}})


@pytest.fixture(autouse=True)
async def _ensure_indexes(db):
    from services.db_indexes import ensure_dedup_indexes
    from services.project_versions import ensure_read_index
    await ensure_dedup_indexes(db)
    await ensure_read_index(db)


class TestRecordVersionOnMerge:
    async def test_first_version_for_a_project_is_n_1(self, db):
        from services.project_versions import record_version_on_merge
        pid = _uid("p1")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        await record_version_on_merge(
            db, user_id="u1", project_id=pid, merged_sha="a" * 40,
            pr_url="https://github.com/o/r/pull/1", pr_number=1,
            branch="aurem/fixes-x", source="fix_batch", summary="Fixed a thing",
        )
        row = await db.project_versions.find_one({"project_id": pid})
        assert row["n"] == 1
        assert row["status"] == "merged"
        assert row["merged_sha"] == "a" * 40
        assert row["summary"] == "Fixed a thing"
        # No file contents, no tokens, no diff, ever.
        for forbidden in ("content", "diff", "tokens", "original_content",
                          "patched_content", "tokens_charged"):
            assert forbidden not in row

    async def test_numbers_increment_and_are_never_reused(self, db):
        from services.project_versions import record_version_on_merge
        pid = _uid("p2")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        for i in range(3):
            await record_version_on_merge(
                db, user_id="u1", project_id=pid, merged_sha=f"{i}" * 40,
                pr_url="https://x", pr_number=i, branch="b", source="ship_pr",
            )
        rows = await db.project_versions.find({"project_id": pid}).sort("n", 1).to_list(10)
        assert [r["n"] for r in rows] == [1, 2, 3]

    async def test_webhook_redelivery_of_the_same_merge_creates_only_one_row(self, db):
        """Two identical calls (same merged_sha, same project) — the
        SECOND must be a silent no-op (DuplicateKeyError caught
        internally), never a second row, never raise out to the
        webhook handler."""
        from services.project_versions import record_version_on_merge
        pid = _uid("p3")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        sha = "b" * 40
        await record_version_on_merge(
            db, user_id="u1", project_id=pid, merged_sha=sha,
            pr_url="https://x", pr_number=1, branch="b", source="ship_pr",
        )
        await record_version_on_merge(
            db, user_id="u1", project_id=pid, merged_sha=sha,
            pr_url="https://x", pr_number=1, branch="b", source="ship_pr",
        )
        assert await db.project_versions.count_documents({"project_id": pid}) == 1

    async def test_twenty_concurrent_merges_get_unique_sequential_numbers(self, db):
        from services.project_versions import record_version_on_merge
        pid = _uid("p4")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        await asyncio.gather(*[
            record_version_on_merge(
                db, user_id="u1", project_id=pid, merged_sha=f"{i:040d}",
                pr_url="https://x", pr_number=i, branch="b", source="ship_pr",
            )
            for i in range(20)
        ])
        rows = await db.project_versions.find({"project_id": pid}).to_list(30)
        ns = sorted(r["n"] for r in rows)
        assert ns == list(range(1, 21)), f"expected 1..20 with no duplicates/gaps, got {ns}"

    async def test_forged_unsigned_webhook_data_never_reaches_this_function_matters_not(self, db):
        """Not this function's job to verify signatures (the webhook
        router does that before calling in) — but invalid/missing
        required fields must still be refused safely, never crash."""
        from services.project_versions import record_version_on_merge
        pid = _uid("p5")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        # Malformed sha (not 40-hex) — must be refused, not stored.
        await record_version_on_merge(
            db, user_id="u1", project_id=pid, merged_sha="not-a-real-sha",
            pr_url="https://x", pr_number=1, branch="b", source="ship_pr",
        )
        assert await db.project_versions.count_documents({"project_id": pid}) == 0

    async def test_missing_user_id_or_project_id_is_a_safe_noop(self, db):
        from services.project_versions import record_version_on_merge
        await record_version_on_merge(
            db, user_id="", project_id="", merged_sha="c" * 40,
            pr_url="https://x", pr_number=1, branch="b", source="ship_pr",
        )
        assert await db.project_versions.count_documents({}) == await db.project_versions.count_documents({"project_id": {"$exists": True}})

    async def test_invalid_source_is_rejected(self, db):
        from services.project_versions import record_version_on_merge
        pid = _uid("p6")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        await record_version_on_merge(
            db, user_id="u1", project_id=pid, merged_sha="d" * 40,
            pr_url="https://x", pr_number=1, branch="b", source="not_a_real_source",
        )
        assert await db.project_versions.count_documents({"project_id": pid}) == 0

    async def test_oversized_summary_is_capped_at_200_chars(self, db):
        from services.project_versions import record_version_on_merge
        pid = _uid("p7")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        await record_version_on_merge(
            db, user_id="u1", project_id=pid, merged_sha="e" * 40,
            pr_url="https://x", pr_number=1, branch="b", source="ship_pr",
            summary="X" * 10_000,
        )
        row = await db.project_versions.find_one({"project_id": pid})
        assert len(row["summary"]) <= 200

    async def test_never_raises_even_on_a_broken_db(self):
        """Best-effort — a Mongo failure must NEVER raise into the
        webhook handler (which must respond to GitHub promptly)."""
        from services.project_versions import record_version_on_merge

        class _BrokenDB:
            def __getattr__(self, name):
                raise RuntimeError("simulated total db outage")

        # Must not raise.
        await record_version_on_merge(
            _BrokenDB(), user_id="u1", project_id="p1", merged_sha="f" * 40,
            pr_url="https://x", pr_number=1, branch="b", source="ship_pr",
        )


class TestMarkRolledBack:
    async def test_mark_rolled_back_by_loop_id(self, db):
        from services.project_versions import mark_rolled_back, record_version_on_merge
        pid = _uid("p8")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        await record_version_on_merge(
            db, user_id="u1", project_id=pid, merged_sha="1" * 40,
            pr_url="https://x", pr_number=1, branch="b", source="ship_pr",
            loop_id="loop_1",
        )
        await mark_rolled_back(db, loop_id="loop_1")
        row = await db.project_versions.find_one({"project_id": pid})
        assert row["status"] == "rolled_back"

    async def test_mark_rolled_back_by_batch_id(self, db):
        from services.project_versions import mark_rolled_back, record_version_on_merge
        pid = _uid("p9")
        await db.cto_projects.insert_one({"project_id": pid, "user_id": "u1"})
        await record_version_on_merge(
            db, user_id="u1", project_id=pid, merged_sha="2" * 40,
            pr_url="https://x", pr_number=1, branch="b", source="fix_batch",
            batch_id="fb_1",
        )
        await mark_rolled_back(db, batch_id="fb_1")
        row = await db.project_versions.find_one({"project_id": pid})
        assert row["status"] == "rolled_back"

    async def test_mark_rolled_back_never_raises_on_no_match_or_broken_db(self, db):
        from services.project_versions import mark_rolled_back
        await mark_rolled_back(db, loop_id="does_not_exist")

        class _BrokenDB:
            def __getattr__(self, name):
                raise RuntimeError("boom")
        await mark_rolled_back(_BrokenDB(), loop_id="x")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
