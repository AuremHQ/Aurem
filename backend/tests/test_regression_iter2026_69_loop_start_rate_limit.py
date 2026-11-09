"""tests/test_regression_iter2026_69_loop_start_rate_limit.py

Devil's-advocate audit finding (Phase 5 — rate-limit coverage audit
for LLM-cost-triggering endpoints).

routers/loop.py::start_loop is almost certainly the single most
LLM-expensive endpoint in the product (each call can drive a full
Plan->Execute->Verify->Scan->Ship pipeline). It already has real,
well-built abuse protection: a tier gate (Pro/Team + loop_beta_enabled
required — confirmed live, not founder-only, per the Iter 364
"tiered gate" comment and the Iter 212m-181 gate-parity telemetry),
a kill switch, a read-only intent gate, an ambiguity gate, a
FAILURE-triggered circuit breaker (3+ failures/15min), and a per-user
in-flight CONCURRENCY cap.

None of those bound REQUEST FREQUENCY. A circuit breaker only trips
after repeated failures; a concurrency cap only limits simultaneous
loops. A user (or a scripted/compromised account) can start a loop,
let it incur at least one real LLM call during the Plan phase, cancel
it, and immediately start another — repeating as fast as HTTP
round-trips allow, bounded by nothing.

Confirmed via grep: zero `check_rate_limit`/`check_rate_limit_async`
calls anywhere in routers/loop.py, despite the pattern being
established elsewhere in this exact codebase (routers/fix_pipeline.py
rate-limits its revert endpoints at 20/min per user).

Fix: a per-user rate limit on start_loop, using the SAME
check_rate_limit_async the rest of the codebase already uses — which
already has its own Redis-shared-with-in-memory-fallback design
(confirmed by reading services/rate_limiter.py directly: Redis outage
falls back to the same in-memory sliding-window check_rate_limit(),
not "unlimited" — so this closes the request-frequency gap without
needing a second, redundant backstop mechanism).

Placed early — right after the cheap kill-switch/tier checks, before
the intent/ambiguity gates and well before any real engine/LLM work —
so a rate-limited request costs as little as possible, matching the
existing gates' own stated philosophy ("costs nothing" / "BEFORE any
lock/session/LLM work").
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

_fake_req = type(
    "R", (),
    {"client": type("C", (), {"host": "127.0.0.1"})(), "headers": {},
     "url": type("U", (), {"path": "/api/aurem-dev/loop/start"})()},
)()


def _patch_through_the_gates(monkeypatch, loop_router, loop_beta_mod, loop_safety_mod):
    """Common setup: get a founder-tier user past every gate EXCEPT
    the one under test, short-circuiting the expensive engine work
    the same way the existing founder-gate test does."""
    async def fake_current_dev(_auth):
        return {"user_id": "u_founder", "tier": "founder",
                "is_admin": False, "is_unlimited": False}

    class _FakeDB:
        async def find_one(self, *_a, **_kw):
            return None
    monkeypatch.setattr(loop_router, "current_dev", fake_current_dev)
    monkeypatch.setattr(loop_router, "get_db", lambda: _FakeDB())

    async def fake_check_permission(*_a, **_kw):
        return None
    monkeypatch.setattr(
        "services.team_permissions.check_permission", fake_check_permission,
    )

    async def fake_acquire(*_a, **_kw):
        return False, {"loop_id": "lp_existing"}
    monkeypatch.setattr(loop_safety_mod, "acquire_loop_lock", fake_acquire)

    async def fake_circuit(*_a, **_kw):
        return (False, 0, 0)
    monkeypatch.setattr(loop_safety_mod, "is_loop_circuit_open", fake_circuit)


class TestStartLoopIsRateLimited:
    @pytest.mark.asyncio
    async def test_rate_limited_user_gets_429_before_any_engine_work(self, monkeypatch):
        from routers import loop as loop_router
        from services import loop_beta, loop_safety

        _patch_through_the_gates(monkeypatch, loop_router, loop_beta, loop_safety)

        engine_touched = {"yes": False}

        async def _fake_acquire_that_would_prove_we_got_too_far(*_a, **_kw):
            engine_touched["yes"] = True
            return False, {"loop_id": "lp_existing"}
        monkeypatch.setattr(
            loop_safety, "acquire_loop_lock",
            _fake_acquire_that_would_prove_we_got_too_far,
        )
        monkeypatch.setattr(
            "routers.loop.check_rate_limit_async",
            AsyncMock(return_value=False),
        )

        body = loop_router.StartBody(user_message="ship me a feature")
        with pytest.raises(HTTPException) as exc_info:
            await loop_router.start_loop(
                body=body, request=_fake_req, authorization=None,
            )
        assert exc_info.value.status_code == 429
        assert engine_touched["yes"] is False, (
            "acquire_loop_lock (real engine work) ran despite the rate "
            "limiter rejecting the request — the check isn't early enough"
        )

    @pytest.mark.asyncio
    async def test_rate_limit_key_is_scoped_per_user_not_global(self, monkeypatch):
        from routers import loop as loop_router
        from services import loop_beta, loop_safety

        _patch_through_the_gates(monkeypatch, loop_router, loop_beta, loop_safety)

        seen_keys = []

        async def _fake_rl(key, limit_per_minute):
            seen_keys.append(key)
            return False  # reject immediately either way
        monkeypatch.setattr("routers.loop.check_rate_limit_async", _fake_rl)

        body = loop_router.StartBody(user_message="ship me a feature")
        with pytest.raises(HTTPException):
            await loop_router.start_loop(
                body=body, request=_fake_req, authorization=None,
            )
        assert len(seen_keys) == 1
        assert "u_founder" in seen_keys[0]

    @pytest.mark.asyncio
    async def test_allowed_request_proceeds_past_the_rate_limiter(self, monkeypatch):
        """A request within the limit must still reach the existing
        downstream gates (proven here by reaching the pre-existing
        409 loop_already_running path, same marker the founder-gate
        test itself uses to prove it cleared an earlier gate)."""
        from routers import loop as loop_router
        from services import loop_beta, loop_safety

        _patch_through_the_gates(monkeypatch, loop_router, loop_beta, loop_safety)
        monkeypatch.setattr(
            "routers.loop.check_rate_limit_async",
            AsyncMock(return_value=True),
        )

        body = loop_router.StartBody(user_message="ship me a feature")
        with pytest.raises(HTTPException) as exc_info:
            await loop_router.start_loop(
                body=body, request=_fake_req, authorization=None,
            )
        assert exc_info.value.status_code == 409


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
