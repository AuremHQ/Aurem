"""tests/test_regression_iter2026_17b_stale_entitlement_preflight_report.py

ITEM 1(c) (BLOCKER, High) — independent-review fix round.

READ-ONLY pre-flight report (admin only): lists every dev_users row
where `fix_entitlement` is True but no current active-entitlement
signal backs it (the same "stale" shape
services/fix_entitlement.py::is_fix_entitled now self-heals against,
surfaced here as a list instead of a silent per-request check, for
the founder to review before any real revoke). Does NOT revoke
anything — see services.fix_entitlement.find_stale_entitlements's own
docstring and the mandatory WriteGuardedDB contract test below.
"""
from __future__ import annotations

import csv
import io
import os
import uuid

import pytest
from fastapi import HTTPException

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
    yield database
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})


class TestFindStaleEntitlementsRequiresGuardedDb:
    async def test_raw_db_rejected(self, db):
        from services.fix_entitlement import find_stale_entitlements
        with pytest.raises(TypeError):
            await find_stale_entitlements(db)

    async def test_every_write_method_raises_and_the_scan_still_completes(self, db):
        """Mirrors Part 6's own contract test style: a db where EVERY
        write method raises must not stop this read-only scan."""
        from services.fix_entitlement import find_stale_entitlements
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("guardcheck")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True, "email": "guardcheck@example.com",
        })
        guarded = WriteGuardedDB(db)
        report = await find_stale_entitlements(guarded)
        assert report["count"] >= 1


class TestStaleEntitlementScan:
    async def test_lists_cancelled_stale_user(self, db):
        from services.fix_entitlement import find_stale_entitlements
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("staleone")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True, "email": "stale@example.com",
            "fix_entitlement_source": "stripe_webhook:starter",
        })
        guarded = WriteGuardedDB(db)
        report = await find_stale_entitlements(guarded)
        ids = [r["user_id"] for r in report["rows"]]
        assert u in ids

    async def test_excludes_genuinely_active_paid_user(self, db):
        from services.fix_entitlement import find_stale_entitlements
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("activeok")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "stripe_sub_id": "sub_live",
            "fix_entitlement": True,
        })
        guarded = WriteGuardedDB(db)
        report = await find_stale_entitlements(guarded)
        ids = [r["user_id"] for r in report["rows"]]
        assert u not in ids

    async def test_excludes_cancelled_user_with_positive_balance(self, db):
        from services import wallet
        from services.fix_entitlement import find_stale_entitlements
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("cancelbalok")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })
        await wallet.grant(
            db, user_id=u, bucket="purchased", amount=10,
            idempotency_key=f"preflight_test_{u}", source="test",
        )
        guarded = WriteGuardedDB(db)
        report = await find_stale_entitlements(guarded)
        ids = [r["user_id"] for r in report["rows"]]
        assert u not in ids

    async def test_excludes_users_whose_fix_entitlement_is_false(self, db):
        from services.fix_entitlement import find_stale_entitlements
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("neverentitled")
        await db.dev_users.insert_one({"user_id": u, "tier": "free", "stripe_sub_id": None})
        guarded = WriteGuardedDB(db)
        report = await find_stale_entitlements(guarded)
        ids = [r["user_id"] for r in report["rows"]]
        assert u not in ids

    async def test_excludes_disputed_user_with_active_sub_not_backfill_target(self, db):
        """A frozen (disputed) user with a real active subscription is
        not the 'stale after cancellation' shape this report targets —
        it resolves itself when the dispute closes, via the existing
        freeze mechanism, not a revoke."""
        from services.fix_entitlement import find_stale_entitlements
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("disputedactive")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "starter", "stripe_sub_id": "sub_live",
            "fix_entitlement": True, "fix_entitlement_frozen": True,
        })
        guarded = WriteGuardedDB(db)
        report = await find_stale_entitlements(guarded)
        ids = [r["user_id"] for r in report["rows"]]
        assert u not in ids

    async def test_does_not_write_anything_real(self, db):
        from services.fix_entitlement import find_stale_entitlements
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("nowrite")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })
        guarded = WriteGuardedDB(db)
        await find_stale_entitlements(guarded)
        row = await db.dev_users.find_one({"user_id": u})
        assert row["fix_entitlement"] is True, "pre-flight must never revoke for real"


class TestCsvExport:
    async def test_rows_export_to_csv_injection_safe_with_no_secret_fields(self, db):
        from services.fix_entitlement import find_stale_entitlements
        from services.plan_transition import rows_to_csv
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("csvcheck")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
            "email": "=HYPERLINK(\"http://evil.example\")",
        })
        guarded = WriteGuardedDB(db)
        report = await find_stale_entitlements(guarded)
        csv_text = rows_to_csv(report["rows"])
        reader = csv.reader(io.StringIO(csv_text))
        all_rows = list(reader)
        header = all_rows[0]
        assert not any("secret" in h.lower() or "token" in h.lower() or "password" in h.lower() for h in header)
        for data_row in all_rows[1:]:
            for cell in data_row:
                assert not cell.startswith(("=", "+", "-", "@")), (
                    f"formula-injection cell not escaped: {cell!r}"
                )


class TestAdminEndpoint:
    async def test_non_admin_gets_403(self, monkeypatch):
        from routers import admin_pricing

        async def _fake_require_admin(authorization):
            raise HTTPException(403, "not an admin")
        monkeypatch.setattr(admin_pricing, "_require_admin", _fake_require_admin)
        with pytest.raises(HTTPException) as exc_info:
            await admin_pricing.get_stale_fix_entitlement_preflight(authorization="Bearer not-admin")
        assert exc_info.value.status_code == 403

    async def test_admin_gets_report_wrapped_in_write_guard(self, db, monkeypatch):
        from routers import admin_pricing
        u = _uid("endpointcheck")
        await db.dev_users.insert_one({
            "user_id": u, "tier": "free", "stripe_sub_id": None,
            "fix_entitlement": True,
        })

        async def _fake_require_admin(authorization):
            return {"user_id": "founder_1", "is_founder": True}
        monkeypatch.setattr(admin_pricing, "_require_admin", _fake_require_admin)
        monkeypatch.setattr(admin_pricing, "require_db", lambda: db)

        result = await admin_pricing.get_stale_fix_entitlement_preflight(authorization="Bearer admin")
        ids = [r["user_id"] for r in result["rows"]]
        assert u in ids
        assert result["count"] >= 1
