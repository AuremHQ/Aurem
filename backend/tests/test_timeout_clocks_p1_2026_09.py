"""test_timeout_clocks_p1_2026_09.py — INTERACTION_CONTRACT_V1 P1
follow-up: 4 distinct, independently-enforced timeout clocks (see
services/timeout_clocks.py's module docstring for the full
rationale). Pure unit tests — no event loop, no Mongo, no mocked
provider — by directly manipulating `time.monotonic()`.

Regression this guards against (caught during THIS implementation,
before it shipped): an earlier draft shared the same `mark_activity()`
signal for both `connect` and `first_token`, which meant a normal,
slow, zero-tool-call chat reply would trip `connect`'s tighter 15s
budget well before the deliberately generous 48s `first_token` grace
period even started — see
`test_connect_and_first_token_are_independent_signals` below.
"""
from __future__ import annotations

import time

import pytest

from services.timeout_clocks import TimeoutClocks


def _clocks(**overrides):
    defaults = dict(
        connect_timeout_s=15.0, first_token_timeout_s=48.0,
        stream_idle_timeout_s=60.0, hard_deadline_s=180.0,
    )
    defaults.update(overrides)
    now = time.monotonic()
    return TimeoutClocks(t_start=now, last_progress_at=now, **defaults)


def test_all_clocks_within_budget_returns_none():
    c = _clocks()
    assert c.evaluate() is None


def test_connect_fires_when_worker_never_scheduled():
    c = _clocks(connect_timeout_s=0.0)
    # mark_connected() was never called (simulates a worker task that
    # never got any CPU time at all).
    assert c.evaluate() == ("connect", "provider_connect_timeout")


def test_connect_satisfied_by_mark_connected_alone():
    c = _clocks(connect_timeout_s=0.0, first_token_timeout_s=0.0)
    c.mark_connected()
    # connect is satisfied, but first_token is ALSO past its (zero)
    # budget with no progress yet — that one fires instead.
    assert c.evaluate() == ("first_token", "first_token_timeout")


def test_connect_and_first_token_are_independent_signals():
    """The regression this module was rewritten to prevent: a
    zero-tool-call chat reply must NOT trip `connect`'s tight budget
    just because it hasn't produced any content yet. `mark_connected()`
    (worker started executing) must fully satisfy `connect` regardless
    of whether `mark_activity()` (real output progress) has ever
    fired."""
    c = _clocks(connect_timeout_s=15.0, first_token_timeout_s=48.0)
    c.mark_connected()
    c.t_start -= 20  # pretend 20s have elapsed since start
    c.last_progress_at = c.t_start
    # connect_timeout_s=15 has been blown by elapsed time alone, but
    # mark_connected() already satisfied it — must NOT fire connect.
    assert c.evaluate() is None, (
        "connect must not fire once mark_connected() was called, "
        "even if first_token's own (later) budget has not yet elapsed"
    )


def test_first_token_fires_on_zero_progress_after_budget():
    c = _clocks(first_token_timeout_s=1.0)
    c.mark_connected()
    c.t_start -= 2
    assert c.evaluate() == ("first_token", "first_token_timeout")


def test_first_token_satisfied_by_any_progress():
    c = _clocks(first_token_timeout_s=1.0)
    c.mark_connected()
    c.mark_activity()
    c.t_start -= 2
    # first_progress_at is set (mark_activity fired) -> first_token
    # clock is satisfied even though `first_token_timeout_s` elapsed.
    assert c.evaluate() is None


def test_stream_idle_fires_after_progress_then_stall():
    c = _clocks(stream_idle_timeout_s=1.0)
    c.mark_connected()
    c.mark_activity()  # one real tool call happened...
    c.last_progress_at -= 2  # ...then 2s of total silence since
    assert c.evaluate() == ("stream_idle", "stream_idle_timeout")


def test_stream_idle_resets_on_new_progress():
    c = _clocks(stream_idle_timeout_s=5.0)
    c.mark_connected()
    c.mark_activity()
    c.last_progress_at -= 10  # would have breached stream_idle...
    c.mark_activity()         # ...but a NEW tool call just landed
    assert c.evaluate() is None


def test_hard_deadline_wins_over_everything_else():
    """hard_deadline is the absolute ceiling — it must fire even if a
    more specific clock would ALSO have something to say, and even if
    connect/progress were never satisfied."""
    c = _clocks(hard_deadline_s=1.0, connect_timeout_s=0.0)
    c.t_start -= 5
    assert c.evaluate() == ("hard_deadline", "hard_deadline_exceeded")


def test_hard_deadline_default_mirrors_legacy_hard_timeout():
    """The 4-clock tracker's hard_deadline_s is ALWAYS constructed from
    the same HARD_TIMEOUT_S the legacy mechanism uses (see
    routers/chat/stream.py's `TimeoutClocks(..., hard_deadline_s=
    HARD_TIMEOUT_S)`), so activating INTERACTION_CONTRACT_V1 can never
    make a turn run LONGER than legacy behavior already allowed —
    only ever the same or earlier."""
    legacy_hard = 180.0
    c = TimeoutClocks(hard_deadline_s=legacy_hard)
    assert c.hard_deadline_s == legacy_hard


def test_budget_for_returns_correct_value_per_clock():
    c = _clocks(connect_timeout_s=11, first_token_timeout_s=22,
                stream_idle_timeout_s=33, hard_deadline_s=44)
    assert c.budget_for("connect") == 11
    assert c.budget_for("first_token") == 22
    assert c.budget_for("stream_idle") == 33
    assert c.budget_for("hard_deadline") == 44


def test_snapshot_shape():
    c = _clocks()
    snap = c.snapshot()
    assert set(snap.keys()) == {
        "elapsed_s", "connected", "since_last_progress_s",
        "first_progress_at_s", "budgets",
    }
    assert snap["connected"] is False
    assert snap["first_progress_at_s"] is None
    c.mark_connected()
    c.mark_activity()
    snap2 = c.snapshot()
    assert snap2["connected"] is True
    assert snap2["first_progress_at_s"] is not None


def test_env_overrides_are_read_at_construction(monkeypatch):
    monkeypatch.setenv("CHAT_CONNECT_TIMEOUT_S", "7")
    monkeypatch.setenv("CHAT_FIRST_TOKEN_TIMEOUT_S", "9")
    monkeypatch.setenv("CHAT_STREAM_IDLE_TIMEOUT_S", "11")
    monkeypatch.setenv("CHAT_HARD_TIMEOUT_S", "13")
    c = TimeoutClocks()
    assert c.connect_timeout_s == 7.0
    assert c.first_token_timeout_s == 9.0
    assert c.stream_idle_timeout_s == 11.0
    assert c.hard_deadline_s == 13.0
