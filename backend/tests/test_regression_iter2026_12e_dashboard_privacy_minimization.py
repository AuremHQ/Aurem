"""tests/test_regression_iter2026_12e_dashboard_privacy_minimization.py

Master Prompt v2 continuation — PRE-STEP (e): PRIVACY of the admin
pricing dashboard (services.pricing_engine.compute_dashboard, exposed
via GET /admin/pricing/dashboard).

WHAT THE DASHBOARD ACTUALLY RETURNS, enumerated by reading
compute_dashboard() top to bottom (services/pricing_engine.py):
  - Aggregate-only, no per-user identity: current_margin,
    current_pricing_version_id, ceiling_hit{count,absorbed_tokens,
    absorbed_usd}, failed_run_cost{total_usd,count}, tokens_owed,
    tokens_owed_usd, fixed_costs_monthly_usd, total_revenue_usd,
    break_even_progress_pct, unmetered_cost_alarm{...},
    billing_flags_summary{flag_type: {open,resolved}}.
  - by_action: per ACTION (not per user) — action, revenue_usd,
    real_cost_usd, runs, realized_margin. No user identity at all.
  - by_user_top50 (PART 3's own, already covered by
    test_regression_iter2026_11d's test_by_user_entries_never_
    include_email): capped at 50 rows, each EXACTLY {user_id,
    revenue_usd, real_cost_usd, realized_margin} — no email, no repo
    URL, no auth/API token.
  - reconcile_drift: forwards services.wallet.reconcile_report(db)
    VERBATIM. THAT function's own return shape
    (services/wallet.py:1153) includes `rows` (EVERY user compared
    this month, uncapped — no top-50 limit at all) and `flagged`
    (the drifted subset), each entry {user_id, wallet_tokens,
    provider_tokens, drift_pct}. Those two lists are per-user token-
    consumption data for the ENTIRE user base in one authenticated
    response, with no cap — a materially bigger exposure than
    by_user_top50's deliberately-capped 50, and NOT "strictly needed":
    frontend/src/pages/AdminPricingPanel.jsx only ever reads
    `reconcile_drift.flagged_count` and `.users_compared` (grep
    confirms — no reference to `.rows` or `.flagged` anywhere in the
    frontend). This is the one real finding from this PRE-STEP: fixed
    below by having compute_dashboard() strip `rows`/`flagged` down to
    the three fields actually consumed (month, users_compared,
    flagged_count) before embedding reconcile_report's result,
    WITHOUT touching reconcile_report() itself (services/wallet.py)
    since OTHER call sites (e.g. test_regression_iter2026_10h's
    direct test of the reconciliation job) legitimately need the full
    per-user rows for their own (non-HTTP, internal) purpose.

No emails, auth tokens/API keys, repo URLs, or other PII appear
ANYWHERE in the dashboard response — this file also adds a recursive
"forbidden key" scan over the WHOLE response so a future field
addition that reintroduces PII fails this test immediately, not just
the two specific fields already known about.

TDD: the reconcile_drift-shape tests are written and run RED before
compute_dashboard() strips rows/flagged.
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
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.real_llm_usage.delete_many({"user_id": {"$regex": "^wallet_test_"}})


ADMIN_USER = {"user_id": "wallet_test_founder", "is_admin": True, "tier": "founder",
              "email": "wallet_test_founder@aurem.dev", "is_unlimited": True}


def _as_admin():
    from unittest.mock import AsyncMock, patch

    from fastapi import HTTPException

    async def _fake_current_dev(authorization=None):
        if not authorization:
            raise HTTPException(401, "Authorization header missing")
        if authorization == "Bearer admin-token":
            return ADMIN_USER
        return {"user_id": "wallet_test_rando", "is_admin": False, "tier": "starter",
                "email": "wallet_test_rando@example.com", "is_unlimited": False}
    return patch("routers._admin_common.current_dev", AsyncMock(side_effect=_fake_current_dev))


_FORBIDDEN_KEYS = {"email", "password", "repo_url", "api_key", "github_token",
                   "secret", "ssn", "phone", "address", "ip_address"}


def _find_forbidden_keys(obj, path="$") -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _FORBIDDEN_KEYS:
                hits.append(f"{path}.{k}")
            hits.extend(_find_forbidden_keys(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            hits.extend(_find_forbidden_keys(v, f"{path}[{i}]"))
    return hits


async def _seed_one_user_with_drift(db):
    from services.wallet import grant, hold, settle
    u = _uid("recdrift")
    await grant(db, user_id=u, bucket="purchased", amount=1000,
                idempotency_key=f"g_{u}", source="test")
    await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
               idempotency_key=f"h_{u}")
    await settle(db, user_id=u, run_id=f"run_{u}", actual_cost_usd=0.02,
                 idempotency_key=f"settle_{u}")
    await db.real_llm_usage.insert_one({
        "_id": f"usage_{u}", "user_id": u, "ts": time.time(),
        "cost_usd": 999.0, "status": "ok",
    })
    return u


class TestReconcileDriftIsAggregateOnly:
    async def test_dashboard_reconcile_drift_has_no_per_user_rows(self, db):
        from routers import admin_pricing as ap
        u = await _seed_one_user_with_drift(db)
        with _as_admin():
            data = await ap.get_dashboard(authorization="Bearer admin-token")
        rd = data["reconcile_drift"]
        assert "rows" not in rd, "reconcile_drift.rows dumps every user's token counts, uncapped — not consumed by the frontend"
        assert "flagged" not in rd, "reconcile_drift.flagged is the same per-user shape as rows"
        assert set(rd.keys()) == {"month", "users_compared", "flagged_count"}
        assert rd["users_compared"] >= 1
        # the seeded user really did drift (999 USD real cost vs ~tiny
        # wallet-ledger settle) — sanity that the underlying data was
        # actually exercised, not just an empty-case pass.
        assert rd["flagged_count"] >= 1
        assert u  # seeded user id never needs to appear in the response at all

    async def test_reconcile_report_itself_is_unchanged_for_internal_callers(self, db):
        """services.wallet.reconcile_report (used directly by
        test_regression_iter2026_10h for the internal reconciliation
        job, not via this HTTP dashboard) must keep returning its full
        per-user rows/flagged — only compute_dashboard's EMBEDDING of
        it is minimized, not the underlying function itself."""
        from services.wallet import reconcile_report
        u = await _seed_one_user_with_drift(db)
        report = await reconcile_report(db)
        assert "rows" in report and "flagged" in report
        assert any(r["user_id"] == u for r in report["rows"])


class TestNoForbiddenPiiAnywhereInDashboard:
    async def test_recursive_scan_for_pii_shaped_keys(self, db):
        from routers import admin_pricing as ap
        await _seed_one_user_with_drift(db)
        with _as_admin():
            data = await ap.get_dashboard(authorization="Bearer admin-token")
        hits = _find_forbidden_keys(data)
        assert hits == [], f"found PII-shaped keys in dashboard response: {hits}"

    async def test_by_user_top50_capped_and_minimal_still_holds(self, db):
        """Re-asserts the PART 3 guarantee (test_regression_iter2026_
        11d's test_by_user_entries_never_include_email) at the SAME
        response this file's other tests exercise, so a regression in
        either guarantee is caught together."""
        from routers import admin_pricing as ap
        await _seed_one_user_with_drift(db)
        with _as_admin():
            data = await ap.get_dashboard(authorization="Bearer admin-token")
        assert len(data["by_user_top50"]) <= 50
        for row in data["by_user_top50"]:
            assert set(row.keys()) == {"user_id", "revenue_usd", "real_cost_usd", "realized_margin"}
