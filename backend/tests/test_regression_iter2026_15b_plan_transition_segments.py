"""tests/test_regression_iter2026_15b_plan_transition_segments.py

Master Prompt v2 PART 6.1/6.3 — the dry-run classifier + full dry
run, exercised against a seeded fixture of the 12 user types named
in the spec: paid active, paid cancelled, free active, free dormant,
disputed, refunded, frozen, admin, test user, no email, duplicate
email, huge allowance.

Writes NOTHING to dev_users/wallets — the dry run always receives a
WriteGuardedDB; this file proves that structurally (every dry-run
entry point takes the guard, never the raw db) and behaviorally (the
seeded users are byte-identical before/after running the dry run).

TDD: written and run RED before services/plan_transition.py exists.
"""
from __future__ import annotations

import os
import time
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
        await db.billing_flags.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.plan_transition_fake_applies.delete_many({})


async def _mk_user(db, **fields):
    u = _uid(fields.pop("_tag", "user"))
    doc = {"user_id": u, "tier": "free", "email": f"{u}@example.com", **fields}
    await db.dev_users.insert_one(doc)
    return u


@pytest.fixture
async def seeded_12_types(db):
    """The 12 user types named in the PART 6.3 spec, seeded once per
    test via this fixture."""
    now = time.time()
    ids = {}

    ids["paid_active"] = await _mk_user(
        db, _tag="paidactive", tier="starter", stripe_sub_id="sub_abc123",
        fix_entitlement=True, tokens_granted=0,
    )
    ids["paid_cancelled"] = await _mk_user(
        db, _tag="paidcancelled", tier="free", stripe_sub_id=None,
        fix_entitlement=True, tier_updated_at="2026-09-01T00:00:00+00:00",
    )
    ids["free_active"] = await _mk_user(
        db, _tag="freeactive", tier="free", last_login_at=now - 3600,
    )
    ids["free_dormant"] = await _mk_user(
        db, _tag="freedormant", tier="free", last_login_at=now - 86400 * 400,
    )
    ids["disputed"] = await _mk_user(
        db, _tag="disputed", tier="pro", stripe_sub_id="sub_disp",
        fix_entitlement=True, fix_entitlement_frozen=True,
    )
    await db.billing_flags.insert_one({
        "_id": f"flag_{uuid.uuid4().hex[:10]}", "flag_type": "dispute_freeze",
        "user_id": ids["disputed"], "resolved": False, "created_at": now,
    })
    ids["refunded"] = await _mk_user(
        db, _tag="refunded", tier="free", stripe_sub_id=None,
    )
    await db.billing_flags.insert_one({
        "_id": f"flag_{uuid.uuid4().hex[:10]}", "flag_type": "refund_shortfall",
        "user_id": ids["refunded"], "resolved": True, "created_at": now,
    })
    ids["frozen"] = await _mk_user(
        db, _tag="frozen", tier="starter", stripe_sub_id="sub_frz",
        fix_entitlement=True, fix_entitlement_frozen=True,
    )
    ids["admin"] = await _mk_user(
        db, _tag="admin", tier="founder", is_unlimited=True,
    )
    ids["test_user"] = await _mk_user(
        db, _tag="test", email="test_wallet_test_qa@example.com", tier="free",
    )
    ids["no_email"] = await _mk_user(db, _tag="noemail", email="", tier="free")
    e = f"{_uid('dup')}@example.com"
    ids["dup_1"] = await _mk_user(db, _tag="dupA", email=e, tier="free")
    ids["dup_2"] = await _mk_user(db, _tag="dupB", email=e.upper(), tier="free")
    ids["huge_allowance"] = await _mk_user(
        db, _tag="huge", tier="starter", stripe_sub_id="sub_huge",
        fix_entitlement=True, tokens_granted=50_000_000,
    )
    return ids


class TestClassifyUser:
    async def test_paid_active(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["paid_active"]})
        result = await classify_user(db, user)
        assert result["segment"] == "paid_active"
        assert result["paid"] is True
        assert result["excluded"] is False

    async def test_paid_cancelled(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["paid_cancelled"]})
        result = await classify_user(db, user)
        assert result["segment"] == "paid_cancelled"

    async def test_free_active_vs_dormant(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        active = await db.dev_users.find_one({"user_id": seeded_12_types["free_active"]})
        dormant = await db.dev_users.find_one({"user_id": seeded_12_types["free_dormant"]})
        r_active = await classify_user(db, active)
        r_dormant = await classify_user(db, dormant)
        assert r_active["segment"] == "free_active"
        assert r_dormant["segment"] == "free_dormant"
        assert r_active["paid"] is False
        assert r_dormant["paid"] is False

    async def test_disputed_is_excluded(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["disputed"]})
        result = await classify_user(db, user)
        assert result["segment"] == "disputed"
        assert result["excluded"] is True
        assert "dispute" in result["exclude_reason"].lower()

    async def test_refunded_is_excluded(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["refunded"]})
        result = await classify_user(db, user)
        assert result["segment"] == "refunded"
        assert result["excluded"] is True

    async def test_frozen_is_excluded(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["frozen"]})
        result = await classify_user(db, user)
        assert result["segment"] == "frozen"
        assert result["excluded"] is True

    async def test_admin_segment(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["admin"]})
        result = await classify_user(db, user)
        assert result["segment"] == "admin"
        assert result["excluded"] is True

    async def test_test_user_segment(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["test_user"]})
        result = await classify_user(db, user)
        assert result["segment"] == "test_user"
        assert result["excluded"] is True

    async def test_no_email_segment(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["no_email"]})
        result = await classify_user(db, user, all_emails_lower={})
        assert result["segment"] == "no_email"
        assert result["excluded"] is True

    async def test_duplicate_email_segment(self, db, seeded_12_types):
        from services.plan_transition import build_duplicate_email_index, classify_user
        dup_email_index = await build_duplicate_email_index(db)
        u1 = await db.dev_users.find_one({"user_id": seeded_12_types["dup_1"]})
        u2 = await db.dev_users.find_one({"user_id": seeded_12_types["dup_2"]})
        r1 = await classify_user(db, u1, all_emails_lower=dup_email_index)
        r2 = await classify_user(db, u2, all_emails_lower=dup_email_index)
        assert r1["segment"] == "duplicate_email"
        assert r2["segment"] == "duplicate_email"
        assert r1["excluded"] is True

    async def test_huge_allowance_segment(self, db, seeded_12_types):
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["huge_allowance"]})
        result = await classify_user(db, user)
        assert result["segment"] == "huge_allowance"
        assert result["excluded"] is True

    async def test_no_override_configured_still_computes_a_real_per_tier_grant(self, db, seeded_12_types, monkeypatch):
        """2026-10-25 (ITEM 9, independent review) — corrected from
        the old default ("no rate configured -> needs_decision for
        EVERY paying user"). That default is exactly what this item
        replaces: a paying user on a real, priced, capped tier
        (starter here) now gets a real proposed grant BY DEFAULT, via
        services.plan_transition.tokens_per_old_task_micro's per-tier
        derivation from services.subscription_tiers — no founder
        env-var action required. See
        test_regression_iter2026_23a_plan_transition_per_tier_rate.py
        for the full new-behavior test suite this correction defers
        to (exact worked numbers, override-still-works, free/founder
        never-guess, paid_cancelled unknown-former-tier case)."""
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", None)
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["paid_active"]})
        result = await classify_user(db, user)
        assert result["needs_decision"] is False
        assert result["proposed_token_grant"] is not None
        assert result["proposed_token_grant"] > 0
        assert result["override_active"] is False

    async def test_configured_rate_computes_a_real_proposed_grant(self, db, seeded_12_types, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", 20.0)
        from services.plan_transition import classify_user
        user = await db.dev_users.find_one({"user_id": seeded_12_types["paid_active"]})
        result = await classify_user(db, user)
        assert result["needs_decision"] is False
        assert result["proposed_token_grant"] is not None
        assert result["proposed_token_grant"] >= 0


class TestFullDryRun:
    async def test_dry_run_writes_nothing_and_classifies_everyone(self, db, seeded_12_types):
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB

        snapshot_before = {
            uid: await db.dev_users.find_one({"user_id": uid})
            for uid in seeded_12_types.values() if isinstance(uid, str)
        }

        guarded = WriteGuardedDB(db)
        report = await run_dry_run(guarded)

        for uid, before in snapshot_before.items():
            after = await db.dev_users.find_one({"user_id": uid})
            assert after == before

        assert "rows" in report
        assert "summary" in report
        seeded_ids = set(seeded_12_types.values())
        rows_for_seeded = [r for r in report["rows"] if r["user_id"] in seeded_ids]
        assert len(rows_for_seeded) == len(seeded_ids)

    async def test_summary_has_counts_per_segment_and_total_tokens_and_liability(self, db, seeded_12_types, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", 20.0)
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db)
        report = await run_dry_run(guarded)
        summary = report["summary"]
        assert "counts_per_segment" in summary
        assert "total_tokens_proposed" in summary
        assert "estimated_liability_usd" in summary
        assert summary["counts_per_segment"].get("paid_active", 0) >= 1

    async def test_dry_run_requires_a_write_guarded_db(self, db):
        """Defensive, structural requirement — run_dry_run must refuse
        a raw (unguarded) db outright, never silently accept one."""
        from services.plan_transition import run_dry_run
        with pytest.raises(TypeError):
            await run_dry_run(db)


class TestEdgeCasesAndIdempotency:
    async def test_empty_user_base_does_not_crash(self, db):
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        # Scoped to a user_id prefix that will never match anything real.
        guarded = WriteGuardedDB(db)
        report = await run_dry_run(guarded, user_filter={"user_id": {"$regex": "^wallet_test_nonexistent_prefix_zzz"}})
        assert report["rows"] == []
        assert report["summary"]["counts_per_segment"] == {}

    async def test_double_run_produces_identical_output(self, db, seeded_12_types, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", 20.0)
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db)
        report1 = await run_dry_run(guarded, user_filter={"user_id": {"$in": list(seeded_12_types.values())}})
        report2 = await run_dry_run(guarded, user_filter={"user_id": {"$in": list(seeded_12_types.values())}})
        assert report1["summary"] == report2["summary"]

    async def test_mongo_operator_injection_in_user_filter_is_rejected(self, db):
        """A caller-supplied filter must never be able to smuggle a
        Mongo operator shaped to bypass the test/admin scoping this
        tool is meant for — run_dry_run validates the filter shape."""
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        guarded = WriteGuardedDB(db)
        with pytest.raises(ValueError):
            await run_dry_run(guarded, user_filter={"$where": "function() { return true; }"})
