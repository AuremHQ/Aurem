"""
services/timeout_clocks.py — 4 distinct, independently-enforced timeout
clocks for a chat turn (INTERACTION_CONTRACT_V1 P1 follow-up, ticket
wording: "4 *distinct* timeout clocks (connect / first-token /
stream-idle / hard end-to-end deadline) — today only the pre-existing
~48s soft-timeout + slow-notice ticker exist, not 4 separately-enforced
clocks.").

Prior mechanism (still present, UNCHANGED when INTERACTION_CONTRACT_V1
is OFF): `routers/chat/watchdog.py::consume_worker_queue` has exactly 2
elapsed-since-start measurements (HARD_TIMEOUT_S=180, SOFT_TIMEOUT_S=48
gated on "still <=1 tool call"). Both count from the SAME `t_start` and
neither resets on partial progress, which conflates 4 genuinely
different failure conditions into one signal:

  1. "the worker task never even got scheduled/started" (connect)      — near-instant in this architecture (asyncio.create_task), so this
     clock is a defensive/rare-fire safety net, not a network-connect
     probe — this app's LLM HTTP call happens deep inside the worker,
     not observable at this layer without a much larger rewire. Kept
     honest: documented as rare-fire by design, not oversold.
  2. "the worker started but produced literally zero progress yet"
     (first_token) — this is what the OLD SOFT_TIMEOUT_S measured;
     kept with equivalent semantics/threshold, now a named, separately
     testable clock instead of an inline boolean expression.
  3. "the worker WAS making progress, then stalled" (stream_idle) — a
     genuinely NEW capability. Unlike 1/2/4 it does not count from
     t_start; it resets every time `mark_activity()` fires (a new tool
     call, a real activity-label change). Old behavior had no way to
     catch "1 tool call at t=40s, then silence until the t=180s hard
     ceiling" — this clock catches that in stream_idle_timeout_s.
  4. "this turn has run too long overall, no matter what" (hard_deadline)
     — unchanged HARD_TIMEOUT_S=180 absolute ceiling.

This module is pure (no I/O, no asyncio) so it can be unit-tested by
directly manipulating `time.monotonic()`-based fields — no event loop,
no Mongo, no mocked provider needed. Callers (`watchdog.py`) own
wiring `evaluate()`'s result into the existing abort/persist path.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


@dataclass
class TimeoutClocks:
    t_start: float = field(default_factory=time.monotonic)
    last_progress_at: float = field(default_factory=time.monotonic)
    connected: bool = False
    first_progress_at: float | None = None

    connect_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("CHAT_CONNECT_TIMEOUT_S", "15")))
    first_token_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("CHAT_FIRST_TOKEN_TIMEOUT_S", "48")))
    stream_idle_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("CHAT_STREAM_IDLE_TIMEOUT_S", "60")))
    hard_deadline_s: float = field(
        default_factory=lambda: float(os.environ.get("CHAT_HARD_TIMEOUT_S", "180")))

    def mark_connected(self) -> None:
        """Call ONCE, at the very top of the worker's own execution
        (before any mode-dispatch/LLM/tool work) — confirms the async
        task was actually scheduled and started running, decoupled
        from whether it has produced any OUTPUT yet. This is what
        makes `connect` a genuinely separate, safe-to-fire-early clock
        instead of accidentally aliasing `first_token`'s signal (an
        earlier draft of this module made that mistake — sharing
        `mark_activity()` for both meant a normal, slow, zero-tool-
        call chat reply would trip `connect`'s tighter budget well
        before the deliberately generous `first_token` grace period
        even started, a real regression caught by
        `test_timeout_clocks_p1_2026_09.py::test_connect_and_first_
        token_are_independent_signals` before this shipped)."""
        self.connected = True

    def mark_activity(self) -> None:
        """Call on every observed real progress signal (a new tool
        invocation appended, or the activity label changing away from
        the initial default). Resets the self-resetting `stream_idle`
        clock and satisfies `first_token`. Does NOT gate `connect` —
        see `mark_connected()`."""
        now = time.monotonic()
        self.last_progress_at = now
        if self.first_progress_at is None:
            self.first_progress_at = now

    def evaluate(self) -> tuple[str, str] | None:
        """Returns (clock_name, failure_mode) for the FIRST clock that
        has breached its budget, checked in the order a real failure
        would actually manifest, or None if all 4 are within budget.
        `hard_deadline` is checked first — it is the absolute ceiling
        and must win regardless of which other clock also fired."""
        now = time.monotonic()
        elapsed = now - self.t_start
        if elapsed >= self.hard_deadline_s:
            return ("hard_deadline", "hard_deadline_exceeded")
        if not self.connected and elapsed >= self.connect_timeout_s:
            return ("connect", "provider_connect_timeout")
        if self.first_progress_at is None and elapsed >= self.first_token_timeout_s:
            return ("first_token", "first_token_timeout")
        if self.first_progress_at is not None and (now - self.last_progress_at) >= self.stream_idle_timeout_s:
            return ("stream_idle", "stream_idle_timeout")
        return None

    def budget_for(self, clock_name: str) -> float:
        return {
            "connect": self.connect_timeout_s,
            "first_token": self.first_token_timeout_s,
            "stream_idle": self.stream_idle_timeout_s,
            "hard_deadline": self.hard_deadline_s,
        }[clock_name]

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "elapsed_s": round(now - self.t_start, 1),
            "connected": self.connected,
            "since_last_progress_s": round(now - self.last_progress_at, 1),
            "first_progress_at_s": (
                round(self.first_progress_at - self.t_start, 1)
                if self.first_progress_at is not None else None),
            "budgets": {
                "connect_timeout_s": self.connect_timeout_s,
                "first_token_timeout_s": self.first_token_timeout_s,
                "stream_idle_timeout_s": self.stream_idle_timeout_s,
                "hard_deadline_s": self.hard_deadline_s,
            },
        }
