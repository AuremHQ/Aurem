"""tests/test_regression_iter2026_19b_get_usage_guarded_db_and_admin_masking.py

ITEM 3(c)/3(e) (High) — independent-review fix round.

3(c): services/plan_transition.py line ~211 called
`get_usage(user_id)`, which internally does `db = require_db()` — the
GLOBAL db singleton — completely bypassing whatever WriteGuardedDB
run_dry_run/classify_user were handed. Not a write risk today
(get_usage only reads), but it breaks the "the dry run's own db
access is 100% through the guard" guarantee the rest of PART 6 is
built on, and would silently become a real risk if get_usage were
ever extended to write anything.

Fix: `get_usage(user_id, db=None)` — an optional, backward-compatible
db parameter (every existing caller passes only `user_id` and is
unaffected). `classify_user` now passes its own `db` through.

3(e): the admin dry-run report returns per-user rows with a plain-
text `email` field. Masked to `a***@d***.com` unless the caller is
the founder.
"""
from __future__ import annotations

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
    yield database
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.billing_flags.delete_many({"user_id": {"$regex": "^wallet_test_"}})


class TestGetUsageOptionalDbParam:
    async def test_default_none_uses_global_db_unchanged(self, db, monkeypatch):
        """Backward compatibility: every existing caller passes only
        user_id — behavior must be identical to before."""
        from services import usage
        monkeypatch.setattr(usage, "require_db", lambda: db)
        u = _uid("defaultdb")
        await db.dev_users.insert_one({"user_id": u, "tier": "free", "email": f"{u}@example.com"})
        result = await usage.get_usage(u)
        assert result["tier"] == "free"

    async def test_explicit_db_param_used_instead_of_global(self, db, monkeypatch):
        from services import usage

        def _explode():
            raise AssertionError("require_db() must not be called when an explicit db is passed")
        monkeypatch.setattr(usage, "require_db", _explode)
        u = _uid("explicitdb")
        await db.dev_users.insert_one({"user_id": u, "tier": "free", "email": f"{u}@example.com"})
        result = await usage.get_usage(u, db=db)
        assert result["tier"] == "free"


class TestClassifyUserNeverTouchesGlobalDb:
    async def test_classify_user_against_all_writes_raise_db_still_completes(self, db):
        """Mirrors Part 6's own contract test style: a db where every
        write method raises must not stop this read-only classification
        — proves get_usage's internal calls are all reads, routed
        through the SAME guarded handle."""
        from services.plan_transition import classify_user
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("allwritesraise")
        user_doc = {"user_id": u, "tier": "starter", "email": f"{u}@example.com",
                    "stripe_sub_id": "sub_x"}
        await db.dev_users.insert_one(user_doc)
        guarded = WriteGuardedDB(db)
        row = await classify_user(guarded, user_doc)
        assert row["user_id"] == u

    async def test_run_dry_run_never_calls_require_db(self, db, monkeypatch):
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        u = _uid("neverglobal")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter", "email": f"{u}@example.com",
                                        "stripe_sub_id": "sub_x"})

        def _explode():
            raise AssertionError("require_db() must never be called from inside the dry run")
        import services.usage as usage_mod
        monkeypatch.setattr(usage_mod, "require_db", _explode)

        guarded = WriteGuardedDB(db)
        report = await run_dry_run(guarded, user_filter={"user_id": u})
        assert len(report["rows"]) == 1


class TestAdminEndpointMasksEmailUnlessFounder:
    async def test_mask_email_helper_shape(self):
        from services.plan_transition import mask_email
        assert mask_email("alice@example.com") == "a***@e***.com"
        assert mask_email("bob@sub.domain.co.uk") == "b***@s***.domain.co.uk"
        assert mask_email(None) is None
        assert mask_email("") == ""
        assert mask_email("not-an-email") == "not-an-email"

    async def test_non_founder_admin_sees_masked_emails(self, db, monkeypatch):
        from routers import admin_pricing
        u = _uid("maskcheck")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter", "email": "realuser@example.com",
                                        "stripe_sub_id": "sub_x"})

        async def _fake_require_admin(authorization):
            return {"user_id": "admin_1", "email": "admin@example.com", "tier": "admin"}
        monkeypatch.setattr(admin_pricing, "_require_admin", _fake_require_admin)
        monkeypatch.setattr(admin_pricing, "require_db", lambda: db)

        result = await admin_pricing.get_plan_transition_dry_run(authorization="Bearer admin")
        row = next(r for r in result["rows"] if r["user_id"] == u)
        assert row["email"] == "r***@e***.com"
        assert "realuser@example.com" not in str(result)

    async def test_founder_sees_real_emails(self, db, monkeypatch):
        from routers import admin_pricing
        u = _uid("foundercheck")
        await db.dev_users.insert_one({"user_id": u, "tier": "starter", "email": "realuser@example.com",
                                        "stripe_sub_id": "sub_x"})

        async def _fake_require_admin(authorization):
            return {"user_id": "founder_1", "email": "founder@aurem.dev", "tier": "founder"}
        monkeypatch.setattr(admin_pricing, "_require_admin", _fake_require_admin)
        monkeypatch.setattr(admin_pricing, "require_db", lambda: db)

        result = await admin_pricing.get_plan_transition_dry_run(authorization="Bearer founder")
        row = next(r for r in result["rows"] if r["user_id"] == u)
        assert row["email"] == "realuser@example.com"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
