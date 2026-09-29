"""Testing-agent safety check (Item C): verify persisted lockout still
fires when RATE_LIMIT_DISABLED is unset/absent, and only bypasses when
RATE_LIMIT_DISABLED=1. This is the safety-critical production behaviour."""
from __future__ import annotations

import os
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import HTTPException
import routers.auth as auth_module


@pytest_asyncio.fixture
async def _db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    ip = "203.0.113.66"
    await db.login_attempts.delete_one({"_id": f"ip:{ip}"})
    yield db, ip
    await db.login_attempts.delete_one({"_id": f"ip:{ip}"})
    client.close()


async def _seed(db, ip, n):
    now = datetime.now(timezone.utc)
    await db.login_attempts.update_one(
        {"_id": f"ip:{ip}"},
        {"$set": {"failed_at": [now] * n}},
        upsert=True,
    )


@pytest.mark.asyncio
async def test_prod_lockout_fires_when_rate_limit_disabled_unset(_db, monkeypatch):
    """With RATE_LIMIT_DISABLED unset (production default), 5 failed
    attempts should still lock out the IP."""
    db, ip = _db
    await _seed(db, ip, auth_module._LOGIN_FAIL_LIMIT)
    monkeypatch.setattr(auth_module, "_RATE_LIMIT_DISABLED", False)
    with pytest.raises(HTTPException) as exc:
        await auth_module._enforce_login_guard(db, ip)
    assert exc.value.status_code == 429
    assert "Too many failed logins" in exc.value.detail


@pytest.mark.asyncio
async def test_prod_lockout_below_limit_still_allowed(_db, monkeypatch):
    """Below the failure limit → no lockout even in production mode."""
    db, ip = _db
    await _seed(db, ip, auth_module._LOGIN_FAIL_LIMIT - 1)
    monkeypatch.setattr(auth_module, "_RATE_LIMIT_DISABLED", False)
    # must not raise
    await auth_module._enforce_login_guard(db, ip)
