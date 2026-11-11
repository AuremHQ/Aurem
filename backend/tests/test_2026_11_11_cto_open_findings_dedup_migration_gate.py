"""
test_2026_11_11_cto_open_findings_dedup_migration_gate.py

Locks in the 2026-11-11 deployment_agent fix for
services/db_indexes.py::ensure_dedup_indexes() — the `cto_open_findings`
duplicate-cleanup `delete_many` must run AT MOST ONCE across the life of
the deployment (gated behind a `db_migrations` one-time marker, mirroring
the pre-existing `onboarding_emails` dedup fix a few lines above it in
the same function), NOT unconditionally on every pod boot.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.db_indexes import ensure_dedup_indexes


class _FakeCursor:
    def __init__(self, groups):
        self._groups = groups

    def __aiter__(self):
        async def gen():
            for g in self._groups:
                yield g
        return gen()


def _collection(name):
    """Generic AsyncMock-backed collection stand-in — only needs to
    not blow up for every OTHER collection ensure_dedup_indexes() touches
    (_mk() calls db[coll].create_index(...) via __getitem__)."""
    col = MagicMock()
    col.create_index = AsyncMock(return_value=f"{name}_idx")
    col.aggregate = MagicMock(return_value=_FakeCursor([]))
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
    return col


def _make_fake_db(dup_groups, migration_doc=None):
    collections = {}

    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=lambda name: collections.setdefault(name, _collection(name)))

    _FINDINGS_KEY = "g6_cto_open_findings_dedup_2026_11_11"

    async def _find_one(filter_arg, *a, **kw):
        # Isolate this test to the cto_open_findings migration only —
        # pretend every OTHER migration (e.g. onboarding_emails's, a
        # few lines above it in the same function) already ran, so it
        # doesn't also call db_migrations.update_one and muddy the
        # assertion counts below.
        if filter_arg.get("_id") == _FINDINGS_KEY:
            return migration_doc
        return {"_id": filter_arg.get("_id"), "ran_at": 0.0}

    db.db_migrations = MagicMock()
    db.db_migrations.find_one = AsyncMock(side_effect=_find_one)
    db.db_migrations.update_one = AsyncMock()

    db.cto_open_findings = MagicMock()
    db.cto_open_findings.aggregate = MagicMock(return_value=_FakeCursor(dup_groups))
    delete_result = MagicMock()
    delete_result.deleted_count = sum(max(0, len(g["ids"]) - 1) for g in dup_groups)
    db.cto_open_findings.delete_many = AsyncMock(return_value=delete_result)

    # Attribute-style access (db.onboarding_emails.aggregate(...), etc.)
    # for the other collections ensure_dedup_indexes() reaches via `.`
    # rather than `[]` (only the cto_open_findings pre-dedup block and
    # the onboarding_emails one above it use attribute access).
    db.onboarding_emails = _collection("onboarding_emails")
    db.onboarding_emails.aggregate = MagicMock(return_value=_FakeCursor([]))
    return db


@pytest.mark.asyncio
async def test_dedup_delete_many_runs_on_first_boot_when_no_marker():
    """First-ever boot (no db_migrations row yet): duplicates ARE
    cleaned up and the one-time marker gets written."""
    dup_groups = [
        {"_id": {"user_id": "u1", "project_id": "p1", "finding_id": "f1"},
         "ids": ["a", "b", "c"], "count": 3},
    ]
    db = _make_fake_db(dup_groups, migration_doc=None)

    await ensure_dedup_indexes(db)

    db.cto_open_findings.delete_many.assert_awaited_once()
    called_ids = db.cto_open_findings.delete_many.call_args[0][0]["_id"]["$in"]
    assert called_ids == ["a", "b"], "must keep the LAST id, delete the rest"
    db.db_migrations.update_one.assert_awaited_once()
    (filter_arg, update_arg), _ = db.db_migrations.update_one.call_args
    assert filter_arg == {"_id": "g6_cto_open_findings_dedup_2026_11_11"}
    assert update_arg["$set"]["dup_removed"] == 2


@pytest.mark.asyncio
async def test_dedup_delete_many_skipped_once_marker_exists():
    """This is the exact bug deployment_agent flagged: on every boot
    AFTER the first, the aggregate+delete_many must be skipped entirely
    — no destructive delete_many reachable from a normal pod restart."""
    dup_groups = [
        {"_id": {"user_id": "u1", "project_id": "p1", "finding_id": "f1"},
         "ids": ["a", "b", "c"], "count": 3},
    ]
    db = _make_fake_db(
        dup_groups,
        migration_doc={"_id": "g6_cto_open_findings_dedup_2026_11_11", "ran_at": 123.0},
    )

    await ensure_dedup_indexes(db)

    db.cto_open_findings.aggregate.assert_not_called()
    db.cto_open_findings.delete_many.assert_not_awaited()
    db.db_migrations.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_no_op_when_no_duplicates_found():
    """No duplicate groups at all: delete_many must never be called,
    and the marker is still written (so future boots skip the scan)."""
    db = _make_fake_db(dup_groups=[], migration_doc=None)

    await ensure_dedup_indexes(db)

    db.cto_open_findings.delete_many.assert_not_awaited()
    db.db_migrations.update_one.assert_awaited_once()
    (_, update_arg), _ = db.db_migrations.update_one.call_args
    assert update_arg["$set"]["dup_removed"] == 0
