"""tests/test_regression_iter2026_56_part2c_b1_project_versions_indexes.py

Part 2c B1 — the `project_versions` collection needs TWO unique
constraints (both live in services/db_indexes.py::ensure_dedup_indexes,
"that is where unique constraints live" per the task spec):
  * (project_id, merged_sha) — a webhook redelivery of the same merge
    event must never create a second row.
  * (project_id, n)          — the per-project version NUMBER is
    never reused, even under a race.
Plus a plain (non-unique) read index on (user_id, project_id, n desc)
for the listing query, owned by services/project_versions.py (the
service that actually runs that query) rather than db_indexes.py.

Uses a REAL local Mongo connection — uniqueness constraints are a
database-level guarantee that a dict-based fake DB cannot meaningfully
prove (same rule test_regression_iter2026_10y already follows for
wallet atomicity).
"""
from __future__ import annotations

import os
import uuid

import pytest
from pymongo.errors import DuplicateKeyError

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


def _row(*, project_id, n, merged_sha, user_id="u1"):
    return {
        "version_id": f"pv_{uuid.uuid4().hex[:12]}",
        "user_id": user_id, "project_id": project_id, "n": n,
        "merged_sha": merged_sha, "pr_url": "https://github.com/o/r/pull/1",
        "pr_number": 1, "branch": "aurem/fixes-aaaaaaaa", "source": "fix_batch",
        "summary": "Fixed a thing", "merged_at": 0.0, "status": "merged",
        "created_at": 0.0,
    }


class TestProjectVersionsUniqueIndexes:
    async def test_ensure_dedup_indexes_creates_both_unique_constraints(self, db):
        from services.db_indexes import ensure_dedup_indexes
        await ensure_dedup_indexes(db)
        info = await db.project_versions.index_information()
        assert any(
            {k for k, _ in spec["key"]} == {"project_id", "merged_sha"}
            for spec in info.values() if spec.get("unique")
        ), f"no unique (project_id, merged_sha) index found in {info!r}"
        assert any(
            {k for k, _ in spec["key"]} == {"project_id", "n"}
            for spec in info.values() if spec.get("unique")
        ), f"no unique (project_id, n) index found in {info!r}"

    async def test_duplicate_merged_sha_for_same_project_is_rejected(self, db):
        from services.db_indexes import ensure_dedup_indexes
        await ensure_dedup_indexes(db)
        pid = _uid("p1")
        sha = "a" * 40
        await db.project_versions.insert_one(_row(project_id=pid, n=1, merged_sha=sha))
        with pytest.raises(DuplicateKeyError):
            await db.project_versions.insert_one(_row(project_id=pid, n=2, merged_sha=sha))

    async def test_duplicate_version_number_for_same_project_is_rejected(self, db):
        from services.db_indexes import ensure_dedup_indexes
        await ensure_dedup_indexes(db)
        pid = _uid("p2")
        await db.project_versions.insert_one(_row(project_id=pid, n=1, merged_sha="b" * 40))
        with pytest.raises(DuplicateKeyError):
            await db.project_versions.insert_one(_row(project_id=pid, n=1, merged_sha="c" * 40))

    async def test_different_projects_may_reuse_the_same_n_and_sha_space(self, db):
        from services.db_indexes import ensure_dedup_indexes
        await ensure_dedup_indexes(db)
        pid_a, pid_b = _uid("pa"), _uid("pb")
        await db.project_versions.insert_one(_row(project_id=pid_a, n=1, merged_sha="d" * 40))
        # Same n=1, same merged_sha — but a DIFFERENT project — must succeed.
        await db.project_versions.insert_one(_row(project_id=pid_b, n=1, merged_sha="d" * 40))
        assert await db.project_versions.count_documents({"project_id": pid_a}) == 1
        assert await db.project_versions.count_documents({"project_id": pid_b}) == 1


class TestProjectVersionsReadIndex:
    async def test_read_index_on_user_project_n_desc_exists(self, db):
        from services.project_versions import ensure_read_index
        await ensure_read_index(db)
        info = await db.project_versions.index_information()
        assert any(
            [k for k, _ in spec["key"]] == ["user_id", "project_id", "n"]
            for spec in info.values()
        ), f"no (user_id, project_id, n) read index found in {info!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
