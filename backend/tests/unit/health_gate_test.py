"""
tests/unit/health_gate_test.py — 2026-09-21 decision-point audit lock-in.

Confirmed via code audit: the deployment health gate is already fully
deterministic (if/else thresholds via `run_check_safely()` + the
green/red/gray 3-state contract in services/health_registry.py) — no
LLM involved, no LLM integration needed. This file locks that
behavior in so a future refactor can't silently break it.
"""
from __future__ import annotations

import asyncio

import pytest

from services import health_registry as hr
from services.health_registry import HealthCheck, run_check_safely, result_green, result_gray


async def _healthy_check() -> dict:
    return result_green("all good")


async def _unconfigured_check() -> dict:
    return result_gray("no API key set — not a failure")


async def _slow_check() -> dict:
    await asyncio.sleep(100)
    return result_green("should never get here")  # pragma: no cover


async def _crashing_check() -> dict:
    raise RuntimeError("boom")


async def _bad_shape_check() -> dict:
    return {"status": "purple", "detail": "nonsense"}


@pytest.mark.asyncio
async def test_healthy_check_returns_green():
    check = HealthCheck(id="t-healthy", name="T", category="infra", check_fn=_healthy_check)
    result = await run_check_safely(check)
    assert result["status"] == "green"
    assert result["detail"] == "all good"


@pytest.mark.asyncio
async def test_unconfigured_check_returns_gray_not_red():
    """Missing config must be gray, never collapsed into red — see
    module docstring's 'Never collapse gray into red' rule."""
    check = HealthCheck(id="t-unconfigured", name="T", category="integration",
                         check_fn=_unconfigured_check)
    result = await run_check_safely(check)
    assert result["status"] == "gray"


@pytest.mark.asyncio
async def test_timeout_returns_red(monkeypatch):
    """A check that never returns must be classified red (a slow/
    hanging check IS a real problem), not silently ignored. Speeds up
    the hardcoded 8s budget for the test only — same asyncio.TimeoutError
    path, just doesn't burn 8 real seconds in CI."""
    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro, timeout):
        return await real_wait_for(coro, timeout=0.05)

    monkeypatch.setattr(hr.asyncio, "wait_for", _fast_wait_for)
    check = HealthCheck(id="t-timeout", name="T", category="infra", check_fn=_slow_check)
    result = await run_check_safely(check)
    assert result["status"] == "red"
    assert "timed out" in result["detail"]


@pytest.mark.asyncio
async def test_crashing_check_returns_red_and_never_raises():
    """A check_fn that raises must not propagate — run_check_safely
    catches it and reports red with the exception detail."""
    check = HealthCheck(id="t-crash", name="T", category="guard", check_fn=_crashing_check)
    result = await run_check_safely(check)
    assert result["status"] == "red"
    assert "crashed" in result["detail"]
    assert "RuntimeError" in result["detail"]


@pytest.mark.asyncio
async def test_bad_status_value_is_treated_as_red():
    """A check_fn returning an out-of-contract status value is a bug
    in the check itself — must fail loud as red, never pass through."""
    check = HealthCheck(id="t-badshape", name="T", category="guard",
                         check_fn=_bad_shape_check)
    result = await run_check_safely(check)
    assert result["status"] == "red"
    assert "bad status" in result["detail"]


def test_category_validation_rejects_unknown_category():
    with pytest.raises(ValueError):
        HealthCheck(id="t-bad-cat", name="T", category="nonsense",
                    check_fn=_healthy_check)
