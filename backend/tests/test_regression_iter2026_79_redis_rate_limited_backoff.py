"""
Regression — iter 2026_79 · services/rate_limiter.py backoff on an
Upstash "temporarily rate-limited" response.

Bug: production logs (2026-11-17) showed `_ensure_redis()` retrying
Upstash every ~30-60s after it returned:
  "Your database has been temporarily rate-limited, please contact
   support@upstash.com for further details."
That message is Upstash's own backoff signal, not a generic timeout —
retrying on the same short `_REDIS_RETRY_COOLDOWN_S` (30s) used for
ordinary network blips just kept adding load to an endpoint that had
already asked callers to slow down, with zero chance of success in
that window.

Fix: detect this specific response and gate the NEXT reconnect attempt
behind the longer `_REDIS_RATE_LIMITED_COOLDOWN_S` (5 min) instead.
Ordinary errors (timeout, connection refused, generic quota errors)
must keep using the original short cooldown — this is a backoff fix
for one specific, named Upstash response, not a general slowdown.
"""
from __future__ import annotations

import logging
import time

import pytest


def _reset(monkeypatch, rl):
    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_REDIS_CLIENT", None)
    monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", False)
    monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR", None)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR_WAS_RATE_LIMITED", False)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_SIG", None)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_MINUTE", 0)
    monkeypatch.setattr(rl, "_REDIS_WARN_SUPPRESSED_COUNT", 0)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379")


@pytest.mark.asyncio
async def test_upstash_rate_limited_response_sets_the_long_cooldown(monkeypatch, caplog):
    """The exact Upstash wording from the production logs flips the
    rate-limited flag and the diagnostic reports the long cooldown."""
    from services import rate_limiter as rl

    _reset(monkeypatch, rl)
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)

    async def _fail_ping(self):
        raise ConnectionError(
            "Your database has been temporarily rate-limited, please "
            "contact support@upstash.com for further details."
        )

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _fail_ping)

    with caplog.at_level(logging.WARNING, logger="services.rate_limiter"):
        client = await rl._ensure_redis()

    assert client is None
    assert rl._REDIS_LAST_ERROR_WAS_RATE_LIMITED is True
    diag = rl.redis_diag()
    assert diag["rate_limited_by_provider"] is True
    assert diag["retry_cooldown_s"] == rl._REDIS_RATE_LIMITED_COOLDOWN_S


@pytest.mark.asyncio
async def test_rate_limited_error_blocks_retry_within_5_minutes_but_not_after(monkeypatch):
    """A reconnect attempt inside the 5-minute window is skipped
    entirely (no new handshake dialed); one after the window elapses
    is allowed through again."""
    from services import rate_limiter as rl

    _reset(monkeypatch, rl)
    attempts = {"n": 0}

    async def _fail_ping(self):
        attempts["n"] += 1
        raise ConnectionError(
            "Your database has been temporarily rate-limited, please "
            "contact support@upstash.com for further details."
        )

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _fail_ping)

    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)
    await rl._ensure_redis()
    assert attempts["n"] == 1

    # 60s later — well past the ordinary 30s cooldown, but inside the
    # 5-minute rate-limited one. Must NOT dial a second handshake.
    monkeypatch.setattr(time, "time", lambda: 1_800_000_060.0)
    client = await rl._ensure_redis()
    assert client is None
    assert attempts["n"] == 1, "retried inside the rate-limited cooldown window"

    # 301s later — past the 5-minute window. Retry is allowed again.
    monkeypatch.setattr(time, "time", lambda: 1_800_000_301.0)
    await rl._ensure_redis()
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_ordinary_connection_error_keeps_the_short_30s_cooldown(monkeypatch):
    """No regression: a plain connection/timeout error (not Upstash's
    rate-limit wording) still uses the original short cooldown, so
    ordinary blips recover as quickly as before this fix."""
    from services import rate_limiter as rl

    _reset(monkeypatch, rl)
    attempts = {"n": 0}

    async def _fail_ping(self):
        attempts["n"] += 1
        raise ConnectionError("Connection refused")

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _fail_ping)

    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)
    await rl._ensure_redis()
    assert attempts["n"] == 1
    assert rl._REDIS_LAST_ERROR_WAS_RATE_LIMITED is False

    # 31s later — past the short 30s cooldown. Retry allowed, same as
    # pre-fix behavior for ordinary errors.
    monkeypatch.setattr(time, "time", lambda: 1_800_000_031.0)
    await rl._ensure_redis()
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_successful_reconnect_clears_the_rate_limited_flag(monkeypatch):
    """Once Redis actually comes back, the flag resets so a later,
    unrelated failure isn't mistakenly held to the long cooldown."""
    from services import rate_limiter as rl

    _reset(monkeypatch, rl)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR_WAS_RATE_LIMITED", True)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR", "ResponseError: rate-limited")
    monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", 1_800_000_000.0 - 400)
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)

    async def _ok_ping(self):
        return True

    async def _ok_script_load(self, script):
        return "deadbeef"

    from redis import asyncio as aioredis
    monkeypatch.setattr(aioredis.Redis, "ping", _ok_ping)
    monkeypatch.setattr(aioredis.Redis, "script_load", _ok_script_load)

    client = await rl._ensure_redis()
    assert client is not None
    assert rl._REDIS_LAST_ERROR_WAS_RATE_LIMITED is False
    assert rl.redis_diag()["retry_cooldown_s"] == rl._REDIS_RETRY_COOLDOWN_S
