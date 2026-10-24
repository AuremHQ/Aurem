"""tests/test_regression_iter2026_12d_admin_pricing_rate_limit_and_lockout.py

Master Prompt v2 continuation — PRE-STEP (d): rate limit + lockout on
the NEW admin pricing-mutation endpoints (margin/confirm, fixed-costs
POST/DELETE, billing-flags/{id}/resolve).

WHAT THE GLOBAL MIDDLEWARE ACTUALLY DOES (verified by reading
main.py's `_global_rate_limit_guard`, not assumed):
  - It is a single PER-IP sliding-window limiter
    (`check_rate_limit_async(f"global-ip:{ip}", _GLOBAL_RL_PER_MIN)`),
    default 300 requests/minute, env-tunable via
    GLOBAL_RATE_LIMIT_PER_MIN, applied to every request whose path is
    not in `_GLOBAL_RL_SKIP_PREFIXES` (health checks, chat/loop SSE
    streams, the frontend error-report beacon). `/admin/pricing/*` is
    NOT in that skip list, so it IS covered.
  - It is Redis-shared across pods when REDIS_URL is set, else an
    in-memory per-process bucket (this test env has no REDIS_URL, so
    it exercises the in-memory fallback — same code path, smaller
    blast radius).
  - It is fail-open: any exception from the Redis check is caught and
    treated as "allowed" so a cache outage never blocks real traffic.
  - Critically, it has NO notion of admin identity at all — it keys
    purely on client IP. Two different admin accounts behind the same
    office IP share one 300/min bucket; a single admin rotating
    through many IPs is not slowed down by it at all. Per HARD RULES
    v2 (d)'s explicit ask ("add or verify a per-admin and per-IP
    limit"), this file adds the missing per-admin-id layer (plus a
    tighter, admin-pricing-specific per-IP layer) directly in
    routers/admin_pricing.py, rather than relying on the global
    per-IP-only middleware alone.

WHAT THIS TURN ADDS (routers/admin_pricing.py):
  - Layer 1 (burst): `_enforce_admin_mutation_rate_limit` — a per-
    admin-id bucket AND a per-IP bucket (both via the same
    services.rate_limiter.check_rate_limit_async primitive routers/
    auth.py's login guard already uses), applied to all four mutation
    endpoints: margin/confirm, fixed-costs POST, fixed-costs DELETE,
    billing-flags/{id}/resolve.
  - Layer 2 (lockout, margin/confirm only — the one endpoint with a
    guessable secret, the preview token's HMAC, and a validated input,
    the margin value): a Mongo-persisted failed-attempt counter
    (`admin_pricing_confirm_attempts`), same shape as routers/auth.py's
    `/auth/login` lockout — ADMIN_PRICING_CONFIRM_FAIL_LIMIT (default
    5) failed confirms within ADMIN_PRICING_CONFIRM_LOCKOUT_MIN
    (default 15) minutes locks out ALL further confirm attempts from
    that admin id, even otherwise-valid ones, for the remainder of the
    window. Cleared on the next successful confirm.
  - Both honour RATE_LIMIT_DISABLED=1 for Layer 2 (Layer 1 already
    does, inside check_rate_limit_async itself) — same reasoning as
    test_regression_iter1_login_lockout_respects_rate_limit_disabled.

TDD: written and run RED before the above exists.
"""
from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request

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
        await db.pricing_versions.delete_many({"actor": {"$regex": "^wallet_test_"}})
        await db.pricing_audit_log.delete_many({"actor": {"$regex": "^wallet_test_"}})
        await db.pricing_audit_chain_head.delete_many({})
        await db.pricing_preview_used.delete_many({})
        await db.pricing_seq.delete_many({})
        await db.admin_pricing_confirm_attempts.delete_many({})
        await db.fixed_costs.delete_many({"created_by": {"$regex": "^wallet_test_"}})


ADMIN_A = {"user_id": "wallet_test_founder_a", "is_admin": True, "tier": "founder",
           "email": "wallet_test_founder_a@aurem.dev", "is_unlimited": True}


def _as_admin():
    async def _fake_current_dev(authorization=None):
        if not authorization:
            raise HTTPException(401, "Authorization header missing")
        if authorization == "Bearer admin-token-a":
            return ADMIN_A
        return {"user_id": "wallet_test_rando", "is_admin": False, "tier": "starter",
                "email": "wallet_test_rando@example.com", "is_unlimited": False}
    return patch("routers._admin_common.current_dev", AsyncMock(side_effect=_fake_current_dev))


def _fake_request(ip: str = "203.0.113.5") -> Request:
    scope = {
        "type": "http", "method": "POST", "path": "/admin/pricing/margin/confirm",
        "headers": [(b"x-forwarded-for", ip.encode())],
        "client": (ip, 12345), "query_string": b"", "server": ("test", 80),
        "scheme": "http",
    }
    return Request(scope)


async def _preview_and_get_token(margin: float):
    from routers import admin_pricing as ap
    with _as_admin():
        preview = await ap.preview_margin(
            ap.MarginPreviewBody(margin=margin), authorization="Bearer admin-token-a",
        )
    return preview["preview"]


class TestPerAdminBurstRateLimit:
    async def test_confirm_margin_429s_after_the_per_admin_burst_limit(self, db, monkeypatch):
        from routers import admin_pricing as ap
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_MIN", 2)
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_IP_PER_MIN", 1000)
        request = _fake_request()
        hit_429 = False
        with _as_admin():
            for i in range(5):
                p = await _preview_and_get_token(0.3 + i / 1000)
                try:
                    await ap.confirm_margin(
                        ap.MarginConfirmBody(
                            margin=0.3 + i / 1000, stripe_fee_pct=p["stripe_fee_pct"],
                            stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                            expires_at=p["expires_at"], token=p["token"],
                        ),
                        authorization="Bearer admin-token-a", request=request,
                    )
                except HTTPException as e:
                    if e.status_code == 429:
                        hit_429 = True
                        break
        assert hit_429, "expected a 429 once the per-admin burst limit was exceeded"

    async def test_fixed_cost_writes_are_also_rate_limited_per_admin(self, db, monkeypatch):
        from routers import admin_pricing as ap
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_MIN", 2)
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_IP_PER_MIN", 1000)
        request = _fake_request()
        hit_429 = False
        with _as_admin():
            for i in range(5):
                try:
                    await ap.upsert_fixed_cost(
                        ap.FixedCostBody(name=f"wallet_test_cost_{i}", amount_usd_monthly=10.0),
                        authorization="Bearer admin-token-a", request=request,
                    )
                except HTTPException as e:
                    if e.status_code == 429:
                        hit_429 = True
                        break
        assert hit_429


class TestPerIpBurstRateLimit:
    async def test_confirm_margin_429s_after_the_per_ip_burst_limit_even_with_loose_per_admin(self, db, monkeypatch):
        from routers import admin_pricing as ap
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_MIN", 1000)
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_IP_PER_MIN", 2)
        request = _fake_request()
        hit_429 = False
        with _as_admin():
            for i in range(5):
                p = await _preview_and_get_token(0.3 + i / 1000)
                try:
                    await ap.confirm_margin(
                        ap.MarginConfirmBody(
                            margin=0.3 + i / 1000, stripe_fee_pct=p["stripe_fee_pct"],
                            stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                            expires_at=p["expires_at"], token=p["token"],
                        ),
                        authorization="Bearer admin-token-a", request=request,
                    )
                except HTTPException as e:
                    if e.status_code == 429:
                        hit_429 = True
                        break
        assert hit_429


class TestConfirmFailureLockout:
    async def test_repeated_failed_confirms_lock_out_further_attempts(self, db, monkeypatch):
        from routers import admin_pricing as ap
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_MIN", 1000)
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_IP_PER_MIN", 1000)
        monkeypatch.setattr(ap, "_ADMIN_CONFIRM_FAIL_LIMIT", 3)
        monkeypatch.setattr(ap, "_ADMIN_CONFIRM_LOCKOUT_MIN", 15)
        request = _fake_request()
        statuses = []
        with _as_admin():
            for _ in range(3):
                try:
                    await ap.confirm_margin(
                        ap.MarginConfirmBody(
                            margin=0.4, stripe_fee_pct=0.03,
                            stripe_fee_fixed_usd=0.3,
                            expires_at=time.time() + 60, token="not-a-real-token",
                        ),
                        authorization="Bearer admin-token-a", request=request,
                    )
                except HTTPException as e:
                    statuses.append(e.status_code)
            # One more attempt, even with a VALID preview+token this
            # time — must still be rejected because the admin is
            # locked out.
            p = await _preview_and_get_token(0.42)
            try:
                await ap.confirm_margin(
                    ap.MarginConfirmBody(
                        margin=0.42, stripe_fee_pct=p["stripe_fee_pct"],
                        stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                        expires_at=p["expires_at"], token=p["token"],
                    ),
                    authorization="Bearer admin-token-a", request=request,
                )
                locked_out = False
            except HTTPException as e:
                locked_out = e.status_code == 429

        assert all(s == 409 for s in statuses), f"expected all 3 forged-token attempts to 409, got {statuses}"
        assert locked_out, "a valid confirm after 3 failures should still be locked out"

    async def test_successful_confirm_clears_the_failure_counter(self, db, monkeypatch):
        from routers import admin_pricing as ap
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_MIN", 1000)
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_IP_PER_MIN", 1000)
        monkeypatch.setattr(ap, "_ADMIN_CONFIRM_FAIL_LIMIT", 3)
        request = _fake_request()
        with _as_admin():
            for _ in range(2):
                with pytest.raises(HTTPException):
                    await ap.confirm_margin(
                        ap.MarginConfirmBody(
                            margin=0.4, stripe_fee_pct=0.03, stripe_fee_fixed_usd=0.3,
                            expires_at=time.time() + 60, token="not-a-real-token",
                        ),
                        authorization="Bearer admin-token-a", request=request,
                    )
            p = await _preview_and_get_token(0.46)
            confirmed = await ap.confirm_margin(
                ap.MarginConfirmBody(
                    margin=0.46, stripe_fee_pct=p["stripe_fee_pct"],
                    stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                    expires_at=p["expires_at"], token=p["token"],
                ),
                authorization="Bearer admin-token-a", request=request,
            )
            assert confirmed["ok"] is True

            row = await db.admin_pricing_confirm_attempts.find_one({"_id": "wallet_test_founder_a@aurem.dev"})
            assert not row or not row.get("failed_at")

    async def test_rate_limit_disabled_env_bypasses_the_lockout_layer(self, db, monkeypatch):
        from routers import admin_pricing as ap
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_MIN", 1000)
        monkeypatch.setattr(ap, "_ADMIN_MUTATE_RATE_PER_IP_PER_MIN", 1000)
        monkeypatch.setattr(ap, "_ADMIN_CONFIRM_FAIL_LIMIT", 1)
        monkeypatch.setattr(ap, "_RATE_LIMIT_DISABLED", True)
        request = _fake_request()
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.confirm_margin(
                    ap.MarginConfirmBody(
                        margin=0.4, stripe_fee_pct=0.03, stripe_fee_fixed_usd=0.3,
                        expires_at=time.time() + 60, token="not-a-real-token",
                    ),
                    authorization="Bearer admin-token-a", request=request,
                )
            assert exc.value.status_code == 409  # bad-token error, NOT a lockout

            p = await _preview_and_get_token(0.47)
            confirmed = await ap.confirm_margin(
                ap.MarginConfirmBody(
                    margin=0.47, stripe_fee_pct=p["stripe_fee_pct"],
                    stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                    expires_at=p["expires_at"], token=p["token"],
                ),
                authorization="Bearer admin-token-a", request=request,
            )
        assert confirmed["ok"] is True
