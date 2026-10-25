"""tests/test_regression_iter2026_15d_plan_transition_admin_endpoint.py

Master Prompt v2 PART 6.1/6.3/6.4 — the admin-only read endpoint
wrapping run_dry_run. Reuses routers/admin_pricing.py's existing
router-level `Depends(require_admin_dep)` gate (owner-only, same as
every other route on that router) rather than building a new auth
mechanism.

Security coverage: non-admin gets 403; logged-out gets 401; the
endpoint never accepts a client-supplied `user_filter` that could
smuggle a Mongo operator or scope the report to another tenant's
view (PART 6.4 red-team — there is no per-tenant concept here at
all, since this is a founder-only aggregate report, but the
endpoint's OWN filter is hardcoded, never client-controlled, closing
that avenue outright).
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

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


ADMIN_USER = {"user_id": "wallet_test_founder", "is_admin": True, "tier": "founder",
              "email": "wallet_test_founder@aurem.dev", "is_unlimited": True}


def _as_admin():
    async def _fake_current_dev(authorization=None):
        if not authorization:
            raise HTTPException(401, "Authorization header missing")
        if authorization == "Bearer admin-token":
            return ADMIN_USER
        return {"user_id": "wallet_test_rando", "is_admin": False, "tier": "starter",
                "email": "wallet_test_rando@example.com", "is_unlimited": False}
    return patch("routers._admin_common.current_dev", AsyncMock(side_effect=_fake_current_dev))


class TestAdminEndpointAuth:
    async def test_non_admin_gets_403(self, db):
        from routers import admin_pricing as ap
        with _as_admin(), pytest.raises(HTTPException) as exc:
            await ap.get_plan_transition_dry_run(authorization="Bearer some-other-users-token")
        assert exc.value.status_code == 403

    async def test_logged_out_gets_401(self, db):
        from routers import admin_pricing as ap
        with _as_admin(), pytest.raises(HTTPException) as exc:
            await ap.get_plan_transition_dry_run(authorization=None)
        assert exc.value.status_code == 401

    async def test_admin_gets_a_real_report(self, db):
        from routers import admin_pricing as ap
        u = f"wallet_test_dryrunep_{uuid.uuid4().hex[:8]}"
        await db.dev_users.insert_one({"user_id": u, "tier": "free", "email": f"{u}@example.com"})
        with _as_admin():
            result = await ap.get_plan_transition_dry_run(authorization="Bearer admin-token")
        assert "rows" in result
        assert "summary" in result
        assert any(r["user_id"] == u for r in result["rows"])


class TestAdminEndpointHasNoClientControlledFilter:
    async def test_endpoint_signature_takes_no_filter_parameter(self):
        """The endpoint must not accept a client-supplied query/body
        filter at all — closing off any Mongo-operator-injection or
        cross-scope-widening avenue before it could exist."""
        import inspect

        from routers import admin_pricing as ap
        sig = inspect.signature(ap.get_plan_transition_dry_run)
        assert "user_filter" not in sig.parameters
        assert "filter" not in sig.parameters
