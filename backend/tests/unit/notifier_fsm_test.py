"""
tests/unit/notifier_fsm_test.py — 2026-09-21 decision-point audit lock-in.

Confirmed via code audit: notification/alert routing (severity +
whether to alert) is already fully deterministic (an explicit truth
table + a 2-tick confirm state machine in
services/health_notifier.py's `_decide_should_notify()` /
`_advance_candidate()` / `_should_fire()`) — no LLM involved, no LLM
integration needed. This file locks that behavior in so a future
refactor can't silently break it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.health_notifier import (
    _decide_should_notify, _advance_candidate, _should_fire, _CONFIRM_TICKS,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── _decide_should_notify — the fire/no-fire truth table ─────────────

@pytest.mark.asyncio
async def test_green_to_red_fires():
    assert await _decide_should_notify(False, "green", "red", {}) is True


@pytest.mark.asyncio
async def test_red_to_green_fires():
    assert await _decide_should_notify(False, "red", "green", {}) is True


@pytest.mark.asyncio
async def test_red_to_red_within_cooldown_suppresses():
    state_row = {
        "last_alert_at": _iso(datetime.now(timezone.utc) - timedelta(minutes=5)),
        "last_alert_to": "red",
    }
    assert await _decide_should_notify(False, "red", "red", state_row) is False


@pytest.mark.asyncio
async def test_red_to_red_after_cooldown_expires_fires_again():
    state_row = {
        "last_alert_at": _iso(datetime.now(timezone.utc) - timedelta(hours=2)),
        "last_alert_to": "red",
    }
    assert await _decide_should_notify(False, "red", "red", state_row) is True


@pytest.mark.asyncio
async def test_red_to_red_never_alerted_before_stays_silent():
    """Pre-existing red at pod boot — a baseline observation, not a
    transition. Must not spam on every startup."""
    assert await _decide_should_notify(False, "red", "red", {}) is False


@pytest.mark.asyncio
async def test_acked_check_never_fires_even_on_green_to_red():
    assert await _decide_should_notify(True, "green", "red", {}) is False


@pytest.mark.asyncio
async def test_gray_transitions_never_fire():
    assert await _decide_should_notify(False, "green", "gray", {}) is False
    assert await _decide_should_notify(False, "gray", "green", {}) is False
    assert await _decide_should_notify(False, "gray", "red", {}) is False
    assert await _decide_should_notify(False, "red", "gray", {}) is False


# ── _advance_candidate — the 2-tick confirm state machine ────────────

def test_confirm_ticks_constant_is_two():
    # This test file's other assertions assume exactly 2 confirm
    # ticks (matching the documented default) — locked explicitly so
    # a silent env/default change doesn't invalidate them unnoticed.
    assert _CONFIRM_TICKS == 2


def test_first_observed_change_is_not_yet_confirmed():
    confirmed, fields = _advance_candidate({}, "green", "red")
    assert confirmed is None
    assert fields["candidate_status"] == "red"
    assert fields["candidate_count"] == 1


def test_second_consecutive_tick_confirms_the_transition():
    state_row = {"candidate_status": "red", "candidate_count": 1}
    confirmed, fields = _advance_candidate(state_row, "green", "red")
    assert confirmed == "red"
    assert fields == {"candidate_status": None, "candidate_count": 0}


def test_flapping_back_to_baseline_before_confirm_resets_silently():
    state_row = {"candidate_status": "red", "candidate_count": 1}
    confirmed, fields = _advance_candidate(state_row, "green", "green")
    assert confirmed is None
    assert fields == {"candidate_status": None, "candidate_count": 0}


def test_flapping_to_a_third_status_restarts_the_candidate_count():
    state_row = {"candidate_status": "red", "candidate_count": 1}
    confirmed, fields = _advance_candidate(state_row, "green", "gray")
    assert confirmed is None
    assert fields["candidate_status"] == "gray"
    assert fields["candidate_count"] == 1


# ── _should_fire — the cooldown gate directly ────────────────────────

@pytest.mark.asyncio
async def test_should_fire_true_for_non_red_target():
    assert await _should_fire({}, "green") is True
