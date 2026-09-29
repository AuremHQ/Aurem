"""
test_regression_iter1_login_lockout_respects_rate_limit_disabled.py

Real bug found while diagnosing quality-gate.yml's "Fitness-function
invariants" job (2026-09-29): routers.auth._enforce_login_guard()'s
Layer 2 (Mongo-persisted failed-login lockout) never checked
RATE_LIMIT_DISABLED, unlike Layer 1 (services.rate_limiter, in-memory).

That job boots the backend as a SEPARATE process from pytest (a real
uvicorn on :8001), so many unrelated test files intentionally logging
in with wrong credentials from the one shared runner IP (127.0.0.1)
accumulated real `login_attempts` rows. After 5 such failures (the
default LOGIN_FAIL_LIMIT), EVERY later test's /auth/login call —
even with correct credentials — got a real 429 "Too many failed
logins from this IP", regardless of RATE_LIMIT_DISABLED=1 already
being set in the job env. Confirmed live: `db.login_attempts` in the
CI-shadowing local run had `_id: "ip:127.0.0.1"` with 5 `failed_at`
entries, and ~70 unrelated FAILED/ERROR results across
test_loop_gate_*, test_prompt_caching_regression, test_rollback_v2_*,
test_jwt_revocation, test_oauth_ora_chat_live_preview, etc. all
tracing back to this one lockout row.

Fix: gate Layer 2 with the same RATE_LIMIT_DISABLED flag Layer 1
already honours, via a module-level `_RATE_LIMIT_DISABLED` constant
in routers/auth.py.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from fastapi import HTTPException
import routers.auth as auth_module


@pytest_asyncio.fixture
async def _lockout_db():
    """Fresh Mongo handle pointed at a throwaway collection namespace."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    test_ip = "203.0.113.55"  # TEST-NET-3, RFC 5737 — never a real client IP
    await db.login_attempts.delete_one({"_id": f"ip:{test_ip}"})
    yield db, test_ip
    await db.login_attempts.delete_one({"_id": f"ip:{test_ip}"})
    client.close()


async def _seed_lockout(db, ip: str, count: int) -> None:
    now = datetime.now(timezone.utc)
    await db.login_attempts.update_one(
        {"_id": f"ip:{ip}"},
        {"$set": {"failed_at": [now] * count, "last_email": "x@test.dev"}},
        upsert=True,
    )


@pytest.mark.asyncio
async def test_regression_iter1_login_lockout_respects_rate_limit_disabled(_lockout_db, monkeypatch):
    """RATE_LIMIT_DISABLED=1 must fully bypass the persisted lockout
    (Layer 2), exactly like it already bypasses the in-memory limiter
    (Layer 1) — this is what let CI's shared-runner-IP live-HTTP tests
    keep logging in after 5 unrelated failed attempts."""
    db, ip = _lockout_db
    await _seed_lockout(db, ip, auth_module._LOGIN_FAIL_LIMIT)

    # Sanity: with the flag OFF, the lockout still fires (we never
    # weakened real production behaviour).
    monkeypatch.setattr(auth_module, "_RATE_LIMIT_DISABLED", False)
    with pytest.raises(HTTPException) as exc_info:
        await auth_module._enforce_login_guard(db, ip)
    assert exc_info.value.status_code == 429

    # With the flag ON (what CI sets), the same locked-out IP must be
    # allowed through — this is the fix under test.
    monkeypatch.setattr(auth_module, "_RATE_LIMIT_DISABLED", True)
    await auth_module._enforce_login_guard(db, ip)  # must not raise
