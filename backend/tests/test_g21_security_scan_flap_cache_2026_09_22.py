"""
tests/test_g21_security_scan_flap_cache_2026_09_22.py

Regression lock for #13: "G21 · Security Scan" flapping on a fixed
~poll schedule, uncorrelated with user activity.

Root cause (same as G18, already fixed 2026-08-21): the health-notifier
polls every check every 45s with an 8s per-check timeout; G21's
run_scan() walks every backend .py file and occasionally crosses 8s →
red → recovers next tick → two founder alerts. G18 was given a 5-min
success-cache; G21 was not. Fix: give G21 the same cache so repeated
polls serve a COMPLETED verdict instead of re-running the scan and
re-risking the timeout.

This test FAILS pre-fix (run_scan called on every poll) and PASSES
after (called once per TTL window).
"""
import asyncio

import pytest

import services.health_checks as hc


def _reset_cache():
    hc._G21_CACHE["result"] = None
    hc._G21_CACHE["computed_at"] = 0.0


def test_g21_scan_is_not_rerun_on_every_poll(monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def fake_run_scan():
        calls["n"] += 1
        return {"pass": True, "supply_chain": {"unpinned_count": 2},
                "misconfig": {"finding_count": 0}}

    # scripts.g21_security_scan is imported lazily inside the check.
    import scripts.g21_security_scan as g21mod
    monkeypatch.setattr(g21mod, "run_scan", fake_run_scan)

    async def _poll_thrice():
        return [await hc._check_g21_security_scan() for _ in range(3)]

    results = asyncio.get_event_loop().run_until_complete(_poll_thrice()) \
        if False else asyncio.run(_poll_thrice())

    assert calls["n"] == 1, f"expensive scan re-ran on every poll (n={calls['n']})"
    # Verdict is stable across polls -> cannot green/red flap within TTL.
    assert all(r == results[0] for r in results)
    assert results[0]["status"] in ("green", "ok", "pass") or "clean" in str(results[0]).lower()


def test_g21_ttl_is_five_minutes():
    # The cache window must comfortably cover many 45s poll ticks.
    assert hc._G21_CACHE_TTL_S >= 300
