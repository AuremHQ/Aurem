"""Regression tests - iter 2026-80: Upstash rate limit seen on EVALSHA.

2026-11-19 production deploy logs showed the rate limiter falling back
to per-process in-memory buckets because Upstash answered
"Your database has been temporarily rate-limited". The 2026-11-17 fix
(iter 2026-79) armed the 300 second cooldown only for failures inside
_ensure_redis (connect / ping / SCRIPT LOAD). A failure on the
mid-request EVALSHA took a different path: it logged an unthrottled
warning, left the cached client marked active, and armed no cooldown,
so every later request re-issued EVALSHA against a database that had
just asked callers to back off, and logged again each time.

These tests drive check_rate_limit_async with a fake client whose
evalsha fails, and assert the failure now arms the same cooldown, is
logged once, and is never retried when it is a rate limit answer.
"""

import asyncio
import logging
import sys
import time

sys.path.insert(0, "/app/backend")

import pytest
from redis import asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError
from unittest.mock import AsyncMock

import services.rate_limiter as rl

RATE_LIMITED_MSG = (
    "Your database has been temporarily rate-limited, please contact "
    "support@upstash.com for further details."
)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Reset module globals and buckets; REDIS_URL set so _ensure_redis
    reaches the fake cached client; jitter off so tests stay fast."""
    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_REDIS_CLIENT", None)
    monkeypatch.setattr(rl, "_REDIS_TRIED", True)
    monkeypatch.setattr(rl, "_REDIS_LUA_SHA", "deadbeef")
    monkeypatch.setattr(rl, "_REDIS_BACKEND_ACTIVE", False)
    monkeypatch.setattr(rl, "_REDIS_LAST_ATTEMPT_TS", None)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR", None)
    monkeypatch.setattr(rl, "_REDIS_LAST_ERROR_WAS_RATE_LIMITED", False)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_SIG", None)
    monkeypatch.setattr(rl, "_REDIS_WARN_LAST_MINUTE", 0)
    monkeypatch.setattr(rl, "_REDIS_WARN_SUPPRESSED_COUNT", 0)
    monkeypatch.setattr(rl, "_REDIS_INIT_JITTER_S", 0)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379")
    rl.reset_buckets_for_tests()
    yield
    rl._REDIS_CLIENT = None
    rl._REDIS_BACKEND_ACTIVE = False
    rl.reset_buckets_for_tests()


def _install_client(evalsha, eval_side_effect=None):
    client = AsyncMock()
    client.evalsha = evalsha
    if eval_side_effect is not None:
        client.eval = AsyncMock(side_effect=eval_side_effect)
    else:
        client.eval = AsyncMock(return_value=1)
    rl._REDIS_CLIENT = client
    rl._REDIS_BACKEND_ACTIVE = True
    return client


def _unavailable_warnings(caplog):
    return [r for r in caplog.records if "Redis unavailable" in r.getMessage()]


@pytest.mark.asyncio
async def test_evalsha_rate_limited_falls_back_and_arms_300s_cooldown(caplog):
    """The 2026-11-19 prod failure, replayed on the EVALSHA path: the
    request is allowed via the in-memory bucket, the client is dropped,
    and the 300 s rate-limited cooldown is armed."""
    client = _install_client(AsyncMock(side_effect=ResponseError(RATE_LIMITED_MSG)))
    with caplog.at_level(logging.WARNING, logger="services.rate_limiter"):
        allowed = await rl.check_rate_limit_async("evalsha-rl-key", 5)
    assert allowed is True
    assert client.evalsha.call_count == 1
    assert rl._REDIS_CLIENT is None
    assert rl._REDIS_BACKEND_ACTIVE is False
    assert rl._REDIS_LAST_ERROR_WAS_RATE_LIMITED is True
    assert rl.in_memory_bucket_count("evalsha-rl-key") == 1
    diag = rl.redis_diag()
    assert diag["rate_limited_by_provider"] is True
    assert diag["retry_cooldown_s"] == rl._REDIS_RATE_LIMITED_COOLDOWN_S
    assert diag["retry_cooldown_s"] == 300.0
    assert len(_unavailable_warnings(caplog)) == 1


@pytest.mark.asyncio
async def test_evalsha_rate_limited_is_not_hammered_within_cooldown(caplog):
    """Two requests while the cooldown is armed: EVALSHA is issued once
    in total, both requests fall back, and exactly one warning is logged."""
    client = _install_client(AsyncMock(side_effect=ResponseError(RATE_LIMITED_MSG)))
    with caplog.at_level(logging.WARNING, logger="services.rate_limiter"):
        first = await rl.check_rate_limit_async("evalsha-hammer-key", 5)
        second = await rl.check_rate_limit_async("evalsha-hammer-key", 5)
    assert first is True
    assert second is True
    assert client.evalsha.call_count == 1
    assert len(_unavailable_warnings(caplog)) == 1


@pytest.mark.asyncio
async def test_evalsha_generic_error_arms_short_cooldown():
    """A non-rate-limited mid-request failure arms the short 30 s
    cooldown instead, and is not retried (not a transient error)."""
    client = _install_client(AsyncMock(side_effect=Exception("kaboom")))
    allowed = await rl.check_rate_limit_async("evalsha-generic-key", 5)
    assert allowed is True
    assert client.evalsha.call_count == 1
    assert rl._REDIS_LAST_ERROR_WAS_RATE_LIMITED is False
    diag = rl.redis_diag()
    assert diag["rate_limited_by_provider"] is False
    assert diag["retry_cooldown_s"] == rl._REDIS_RETRY_COOLDOWN_S
    assert diag["retry_cooldown_s"] == 30.0
    second = await rl.check_rate_limit_async("evalsha-generic-key", 5)
    assert second is True
    assert client.evalsha.call_count == 1


@pytest.mark.asyncio
async def test_evalsha_transient_error_retries_with_backoff_then_succeeds(monkeypatch):
    """A dropped connection is retried (bounded, exponential backoff
    with jitter) and the Redis answer is used - no fallback, no warning,
    no cooldown armed."""
    delays = []

    async def _record_sleep(delay, *args, **kwargs):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _record_sleep)
    client = _install_client(AsyncMock(side_effect=[RedisConnectionError("boom"), 1]))
    allowed = await rl.check_rate_limit_async("evalsha-retry-key", 5)
    assert allowed is True
    assert client.evalsha.call_count == 2
    assert len(delays) == 1
    assert 0.05 <= delays[0] <= 0.10
    assert rl._REDIS_BACKEND_ACTIVE is True
    assert rl._REDIS_LAST_ERROR is None
    assert rl.in_memory_bucket_count("evalsha-retry-key") == 0


@pytest.mark.asyncio
async def test_evalsha_rate_limited_is_never_retried():
    """A rate-limited answer is not a transient blip: EVALSHA is issued
    exactly once even though the retry budget is 3 attempts."""
    client = _install_client(AsyncMock(side_effect=ResponseError(RATE_LIMITED_MSG)))
    allowed = await rl.check_rate_limit_async("evalsha-no-retry-key", 5)
    assert allowed is True
    assert client.evalsha.call_count == 1
    assert rl._REDIS_LAST_ERROR_WAS_RATE_LIMITED is True


@pytest.mark.asyncio
async def test_redis_is_retried_after_rate_limited_cooldown_expires(monkeypatch):
    """After the 300 s cooldown elapses, the next request re-handshakes
    with Redis (ping + SCRIPT LOAD) and uses the Redis answer again."""
    broken = _install_client(AsyncMock(side_effect=ResponseError(RATE_LIMITED_MSG)))
    first = await rl.check_rate_limit_async("evalsha-recovery-key", 5)
    assert first is True
    assert broken.evalsha.call_count == 1
    rl._REDIS_LAST_ATTEMPT_TS = time.time() - 301
    fake = AsyncMock()
    fake.ping.return_value = True
    fake.script_load.return_value = "fakesha"
    fake.evalsha.return_value = 1
    fake.eval.return_value = 1
    calls = []

    def _fake_from_url(*args, **kwargs):
        calls.append(args)
        return fake

    monkeypatch.setattr(aioredis, "from_url", _fake_from_url)
    second = await rl.check_rate_limit_async("evalsha-recovery-key", 5)
    assert second is True
    assert len(calls) == 1
    assert rl._REDIS_BACKEND_ACTIVE is True
    assert rl._REDIS_LAST_ERROR is None
    assert fake.evalsha.call_count == 1


def test_transient_error_classification():
    """Only dropped connections / timeouts are retried; rate-limited and
    other ResponseErrors, and plain programming errors, are not."""
    assert rl._is_transient_redis_error(RedisConnectionError("boom")) is True
    assert rl._is_transient_redis_error(RedisTimeoutError("slow")) is True
    assert rl._is_transient_redis_error(TimeoutError("slow")) is True
    assert rl._is_transient_redis_error(ConnectionError("reset")) is True
    assert rl._is_transient_redis_error(ResponseError(RATE_LIMITED_MSG)) is False
    assert rl._is_transient_redis_error(ResponseError("WRONGTYPE")) is False
    assert rl._is_transient_redis_error(ValueError("bad")) is False
    assert rl._is_rate_limited_error(ResponseError(RATE_LIMITED_MSG)) is True
    assert rl._is_rate_limited_error(ResponseError("WRONGTYPE")) is False
    assert rl._is_rate_limited_error(ConnectionError("reset")) is False
