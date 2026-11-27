"""
tests/test_kit_seed_catalog.py — Visibility Kit 2.0 P0-5.

The seed upserts the prompt's 12 items idempotently and reconciles
with migrations/003_visibility_kit.py: the 10 migration rows keep
their keys/copy, answer_blocks moves advisory weight 7 -> auto
weight 15, product_feed_acp (weight 12, ecommerce-only) and
citation_probe (weight 0, advisory) are added. Total weight 100 -> 120.
"""
import importlib

import pytest

from scripts.seed_visibility_items import (
    CATALOG_ITEMS_12,
    TOTAL_WEIGHT,
    seed_visibility_items,
)


class _FakeCollection:
    def __init__(self):
        self.rows = {}
        self.update_calls = []
        self.indexes = []

    async def update_one(self, filt, update, upsert=False):
        self.update_calls.append((filt, update, upsert))
        key = filt["key"]
        row = dict(self.rows.get(key, {}))
        row.update(update.get("$set", {}))
        if key not in self.rows:
            row.update(update.get("$setOnInsert", {}))
        self.rows[key] = row

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))


class _FakeDB:
    def __init__(self):
        self.visibility_items = _FakeCollection()


def test_seed_catalog_has_12_unique_keys():
    keys = [i["key"] for i in CATALOG_ITEMS_12]
    assert len(keys) == 12
    assert len(set(keys)) == 12
    assert TOTAL_WEIGHT == 120


def test_seed_catalog_reconciles_with_migration_003():
    migration = importlib.import_module(
        "migrations.003_visibility_kit").CATALOG_ITEMS
    by_key = {i["key"]: i for i in CATALOG_ITEMS_12}
    for old in migration:
        assert old["key"] in by_key
        if old["key"] == "answer_blocks":
            assert by_key["answer_blocks"]["weight"] == 15
            assert by_key["answer_blocks"]["mode"] == "auto"
        else:
            assert by_key[old["key"]]["weight"] == old["weight"]
            assert by_key[old["key"]]["name"] == old["name"]


def test_seed_catalog_new_items():
    by_key = {i["key"]: i for i in CATALOG_ITEMS_12}
    assert by_key["product_feed_acp"]["weight"] == 12
    assert by_key["product_feed_acp"]["mode"] == "auto"
    assert by_key["product_feed_acp"]["condition"] == "ecommerce_only"
    assert by_key["citation_probe"]["weight"] == 0
    assert by_key["citation_probe"]["mode"] == "advisory"


@pytest.mark.asyncio
async def test_seed_is_idempotent_upsert_by_key():
    db = _FakeDB()
    first = await seed_visibility_items(db)
    second = await seed_visibility_items(db)
    assert first["upserted"] == 12
    assert second["upserted"] == 12
    assert len(db.visibility_items.rows) == 12  # never duplicates
    assert all(call[2] is True for call in db.visibility_items.update_calls)
    assert db.visibility_items.rows["answer_blocks"]["weight"] == 15


@pytest.mark.asyncio
async def test_seed_dry_run_writes_nothing():
    db = _FakeDB()
    result = await seed_visibility_items(db, dry_run=True)
    assert result["dry_run"] is True
    assert result["upserted"] == 12
    assert db.visibility_items.update_calls == []
    assert db.visibility_items.rows == {}
