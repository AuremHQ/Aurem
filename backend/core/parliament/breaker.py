"""
core/parliament/breaker.py — 2026-09-08 Phase 3 god-class split.

`ParliamentCircuitBreaker` + the module-level singleton. Moved
verbatim out of the single core/parliament.py (zero logic change).
"""
from __future__ import annotations

import logging
import time
from collections import deque

logger = logging.getLogger("aurem-dev.parliament")


class ParliamentCircuitBreaker:
    """Tracks LLM call health.  Opens after `FAILURE_THRESHOLD`
    consecutive failures and stays open for `COOLDOWN_SECONDS`.

    States::

        CLOSED → OPEN → HALF_OPEN → CLOSED (or back to OPEN)

    Behaviour:
      - CLOSED    : every call goes through, results are recorded
      - OPEN      : `should_attempt()` returns False — callers
                    fall back to a single (non-council) LLM call
      - HALF_OPEN : exactly one probe call is allowed.  Success →
                    CLOSED; failure → back to OPEN with a fresh
                    cooldown.
    """

    FAILURE_THRESHOLD  = 3
    TIMEOUT_PER_CALL   = 25       # seconds — per single LLM call
    COOLDOWN_SECONDS   = 45       # OPEN → HALF_OPEN wait
    WINDOW_SECONDS     = 60       # sliding window for observability

    def __init__(self):
        self._state            = "closed"
        self._consec_failures  = 0
        self._opened_at        = 0.0
        self._half_open_probe  = False   # True while a probe is in flight
        self._window: deque    = deque(maxlen=128)   # (ts, ok, latency_ms)
        # Part X fix 8 — the old `self._lock = asyncio.Lock()` was
        # created here and never used anywhere.  It is REMOVED, not
        # adopted: every state-transition method is synchronous, and
        # synchronous code is already atomic under asyncio's
        # single-threaded event loop — acquiring an asyncio.Lock
        # would mean making should_attempt/record_* async and
        # touching every caller (incl. self_heal.py) for zero
        # concurrency benefit.

    # ── State machine ─────────────────────────────────────────────
    @property
    def state(self) -> str:
        # OPEN can auto-transition to HALF_OPEN on read if cooldown
        # has elapsed.  This avoids needing a background task.
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self.COOLDOWN_SECONDS:
                self._state = "half_open"
                self._half_open_probe = False
                logger.info("[circuit_breaker] OPEN → HALF_OPEN "
                            "(cooldown elapsed)")
        return self._state

    def should_attempt(self) -> bool:
        """Returns True iff a call should be attempted right now."""
        st = self.state
        if st == "closed":
            return True
        if st == "open":
            return False
        if st == "half_open":
            # Only one probe at a time.
            if not self._half_open_probe:
                self._half_open_probe = True
                return True
            return False
        return True

    # ── Outcome recording ─────────────────────────────────────────
    def _trim(self, now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def record_success(self, latency_ms: float = 0.0) -> None:
        now = time.monotonic()
        self._trim(now)
        self._window.append((now, True, latency_ms))
        self._consec_failures = 0
        # Part X fix 8 — read through the `state` property, not the
        # raw `_state` field, so a success that lands after the
        # cooldown elapsed still closes the breaker instead of
        # leaving a stale OPEN behind.
        st = self.state
        if st in ("half_open", "open"):
            logger.info("[circuit_breaker] %s → CLOSED (probe succeeded)",
                        st.upper())
            self._state = "closed"
            self._half_open_probe = False

    def record_failure(self, latency_ms: float = 0.0,
                        kind: str = "error") -> None:
        now = time.monotonic()
        self._trim(now)
        self._window.append((now, False, latency_ms))
        self._consec_failures += 1
        # Part X fix 8 — same property read as record_success: a
        # failure that lands after the cooldown elapsed is a
        # HALF_OPEN probe failure (fresh cooldown), not a no-op.
        st = self.state
        if st == "half_open":
            logger.warning("[circuit_breaker] HALF_OPEN probe failed → "
                           "OPEN again (kind=%s)", kind)
            self._state = "open"
            self._opened_at = now
            self._half_open_probe = False
            return
        if (st == "closed"
                and self._consec_failures >= self.FAILURE_THRESHOLD):
            logger.warning(
                "[circuit_breaker] CLOSED → OPEN "
                "(%d consecutive failures; kind=%s)",
                self._consec_failures, kind,
            )
            self._state = "open"
            self._opened_at = now

    def record_neutral(self, latency_ms: float = 0.0) -> None:
        """Part X fix 5 — record a NEUTRAL outcome: the call ran but
        says nothing about provider health (the live case is
        COST_CAP_REACHED — a budget event, not an unhealthy
        provider).  Deliberately touches NEITHER the failure window
        NOR the consecutive-failure counter, and moves NO state
        transition — it only RELEASES a half-open probe, so the next
        `should_attempt()` can try again.  Without this, a cost-cap
        hit on a probe call left `_half_open_probe=True` forever:
        council fan-out stayed bypassed (fallback-only) for the
        whole cap episode, and self-heal — whose only LLM call sits
        behind the gate — could never make its healing call."""
        # Read through the property so an elapsed cooldown still
        # transitions OPEN → HALF_OPEN (and resets the probe) first.
        if self.state == "half_open":
            self._half_open_probe = False

    # ── Stats (for logs / introspection) ─────────────────────────
    def stats(self) -> dict:
        self._trim(time.monotonic())
        oks = sum(1 for _, ok, _ in self._window if ok)
        return {
            # Part X fix 8 — read through the `state` property, not
            # the raw `_state` field: after the 45 s cooldown elapses
            # stats() must report half_open (and trigger the
            # transition) instead of a stale open.
            "state":             self.state,
            "consec_failures":   self._consec_failures,
            "window_total":      len(self._window),
            "window_ok":         oks,
            "window_seconds":    self.WINDOW_SECONDS,
            "cooldown_seconds":  self.COOLDOWN_SECONDS,
            "failure_threshold": self.FAILURE_THRESHOLD,
        }


# Module-level singleton — shared across all Parliament instances.
_GLOBAL_BREAKER = ParliamentCircuitBreaker()
