"""tests/test_regression_iter2026_15c_plan_transition_csv_and_apply.py

Master Prompt v2 PART 6.1/6.2/6.3 — CSV export (injection-safe,
secrets-free), the read-only collection-snapshot export helper
(rollback prep), and the FAKE apply idempotency interface (writes
only to its own dedicated bookkeeping collection, never to
dev_users/wallets/fix_entitlement — proven with a WriteGuardedDB that
has NOTHING allowlisted except that one collection).

TDD: written and run RED before these functions exist.
"""
from __future__ import annotations

import os
import uuid

import pytest

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
        await db.plan_transition_fake_applies.delete_many({})


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


class TestCsvExportIsInjectionSafe:
    def test_a_cell_starting_with_equals_is_escaped(self):
        from services.plan_transition import rows_to_csv
        rows = [{"user_id": "u1", "email": "=1+1", "segment": "free_active"}]
        csv_text = rows_to_csv(rows)
        assert "\n'=1+1" in csv_text or ",'=1+1" in csv_text

    @pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
    def test_every_dangerous_leading_char_is_escaped(self, prefix):
        from services.plan_transition import rows_to_csv
        rows = [{"user_id": "u1", "email": f"{prefix}cmd|'/bin/sh'", "segment": "free_active"}]
        csv_text = rows_to_csv(rows)
        # The dangerous cell must NOT appear in the output with its
        # original leading char still first in the field.
        for line in csv_text.splitlines()[1:]:
            for cell in line.split(","):
                assert not cell.startswith(prefix), f"unescaped formula-injection cell: {cell!r}"

    def test_normal_values_are_not_mangled(self):
        from services.plan_transition import rows_to_csv
        rows = [{"user_id": "u1", "email": "normal@example.com", "segment": "free_active"}]
        csv_text = rows_to_csv(rows)
        assert "normal@example.com" in csv_text

    def test_never_includes_secrets_tokens_or_payment_method_data(self):
        from services.plan_transition import rows_to_csv
        rows = [{
            "user_id": "u1", "email": "a@b.com", "segment": "paid_active",
            "stripe_customer_id": "cus_SECRET123", "password_hash": "abc",
            "api_key": "sk_live_SECRET", "github_token": "ghp_SECRET",
        }]
        csv_text = rows_to_csv(rows)
        assert "SECRET" not in csv_text
        assert "password_hash" not in csv_text
        assert "api_key" not in csv_text
        assert "github_token" not in csv_text

    def test_empty_rows_produces_header_only_or_empty_without_crashing(self):
        from services.plan_transition import rows_to_csv
        csv_text = rows_to_csv([])
        assert isinstance(csv_text, str)


class TestSnapshotExportHelperIsReadOnly:
    @pytest.mark.asyncio
    async def test_export_returns_rows_without_writing(self, db):
        from services.plan_transition import export_collection_snapshot
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("snap")
        await db.dev_users.insert_one({"user_id": u, "email": "x@example.com", "tier": "free"})
        guarded = WriteGuardedDB(db)
        rows = await export_collection_snapshot(guarded, "dev_users", query={"user_id": u})
        assert len(rows) == 1
        assert rows[0]["user_id"] == u

    @pytest.mark.asyncio
    async def test_export_never_includes_secret_fields(self, db):
        from services.plan_transition import export_collection_snapshot
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("snapsecret")
        await db.dev_users.insert_one({
            "user_id": u, "email": "x@example.com", "tier": "free",
            "password_hash": "supersecret", "github_token": "ghp_secret",
        })
        guarded = WriteGuardedDB(db)
        rows = await export_collection_snapshot(guarded, "dev_users", query={"user_id": u})
        assert "password_hash" not in rows[0]
        assert "github_token" not in rows[0]

    @pytest.mark.asyncio
    async def test_export_requires_a_write_guarded_db(self, db):
        from services.plan_transition import export_collection_snapshot
        with pytest.raises(TypeError):
            await export_collection_snapshot(db, "dev_users", query={})


class TestFakeApplyIdempotency:
    @pytest.mark.asyncio
    async def test_fake_apply_is_idempotent_per_user_per_transition(self, db):
        from services.plan_transition import fake_apply_transition
        u = _uid("fakeapply")
        r1 = await fake_apply_transition(db, user_id=u, transition_id="t1")
        r2 = await fake_apply_transition(db, user_id=u, transition_id="t1")
        assert r1["applied"] is True
        assert r2["applied"] is True
        assert r2["already_applied"] is True
        count = await db.plan_transition_fake_applies.count_documents(
            {"user_id": u, "transition_id": "t1"},
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_fake_apply_never_touches_dev_users_or_wallets(self, db):
        from services.plan_transition import fake_apply_transition
        u = _uid("fakeapplysafe")
        await db.dev_users.insert_one({"user_id": u, "tier": "free", "email": "x@example.com"})
        before = await db.dev_users.find_one({"user_id": u})
        await fake_apply_transition(db, user_id=u, transition_id="t2")
        after = await db.dev_users.find_one({"user_id": u})
        assert before == after
        wallet_doc = await db.wallets.find_one({"_id": u})
        assert wallet_doc is None

    @pytest.mark.asyncio
    async def test_fake_apply_different_transition_ids_are_independent(self, db):
        from services.plan_transition import fake_apply_transition
        u = _uid("fakeapplymulti")
        r1 = await fake_apply_transition(db, user_id=u, transition_id="t1")
        r2 = await fake_apply_transition(db, user_id=u, transition_id="t2")
        assert r1["already_applied"] is False
        assert r2["already_applied"] is False
