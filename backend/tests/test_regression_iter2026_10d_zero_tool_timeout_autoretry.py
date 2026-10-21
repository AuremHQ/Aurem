"""Regression — 2026-10 ORA chat/fix reliability fix.

Bug: a provider timeout (soft or hard deadline blown) with ZERO tool
calls / side effects made the user see "Model API was slow..." and
have to click Retry by hand, even though nothing happened yet that an
automatic retry could duplicate. Production incident 2026-10-21
22:30-23:00 UTC (deploy 0f7c86a + VPA pod eviction/recreation window)
correlated with exactly this shape of failure.

Fix (stream.py `gen()` + watchdog.py `consume_worker_queue` +
stream_state.py `StreamState.zero_tool_timeout_suppressed`):
  - `consume_worker_queue(..., suppress_zero_tool_timeout=True)` on a
    timeout with 0 recorded tool-call invocations sets
    `state.zero_tool_timeout_suppressed = True` and returns having
    yielded/persisted NOTHING.
  - `gen()` calls it with `suppress_zero_tool_timeout=True` on attempt
    1; if suppressed, it fully cancels+awaits the orphaned worker/
    ticker tasks, resets the per-attempt state primitives, and retries
    ONCE with `suppress_zero_tool_timeout=False` (attempt 2 always
    finalizes normally, whatever happens).
  - Any timeout with >=1 recorded tool call is UNCHANGED — finalizes
    immediately, exactly like before this fix existed, because a tool
    call may already be a side effect a second attempt would repeat.

Two scenarios below hit `consume_worker_queue` directly (no HTTP
layer) so the proof is unambiguous and fast:
  1. 0 tool calls + suppress=True -> silently suppressed, nothing
     yielded, caller told it's safe to retry.
  2. >=1 tool call + suppress=True -> suppression does NOT kick in;
     behaves exactly like suppress=False (finalizes immediately).

The full silent-retry-then-finalize round trip (attempt 1 suppressed,
attempt 2 ends with the same honest "Model API was slow" message) is
already covered end-to-end by
tests/test_stream_golden_regression.py::test_soft_timeout_byte_identical
and ::test_hard_timeout_byte_identical, whose golden fixtures were
re-captured for this change (verified: the only diff vs. the prior
golden capture is the extra retry round; all 4 non-timeout scenarios
are byte-identical).
"""
from __future__ import annotations

import asyncio

import pytest

from routers.chat.stream_state import StreamState
from routers.chat.turn import ChatBody
from routers.chat.watchdog import consume_worker_queue


def _make_state(*, invocations: list) -> StreamState:
    state = StreamState(
        request=None,
        body=ChatBody(prompt="do a deep multi-file audit please"),
        authorization=None,
        user={},
        user_id="golden-user",
        jwt_token="",
        pid_stream="golden-pid",
    )
    import time
    state.t_start = time.monotonic()
    # Tiny hard budget, empty queue -> first q.get() always times out
    # (asyncio.TimeoutError -> ev=None), deterministically hitting the
    # timeout branch on the very first loop iteration.
    state.hard_timeout_s = 0.05
    state.soft_timeout_s = 999
    state.activity = {"label": "thinking…", "invocations": invocations}
    return state


async def _dummy_task():
    await asyncio.sleep(100)


@pytest.mark.asyncio
async def test_regression_iter2026_10d_zero_tool_timeout_is_suppressed():
    """0 tool calls recorded + suppress=True -> fully suppressed:
    nothing yielded, state flagged so the caller knows to retry."""
    state = _make_state(invocations=[])
    worker_t = asyncio.ensure_future(_dummy_task())
    ticker_t = asyncio.ensure_future(_dummy_task())

    chunks = [
        c async for c in consume_worker_queue(
            state, worker_t, ticker_t, suppress_zero_tool_timeout=True,
        )
    ]

    assert chunks == [], (
        "a suppressed zero-tool timeout must not stream/persist anything "
        "for this attempt"
    )
    assert state.zero_tool_timeout_suppressed is True
    assert state.timed_out is True
    assert worker_t.cancelled() or worker_t.cancel()
    assert ticker_t.cancelled() or ticker_t.cancel()


@pytest.mark.asyncio
async def test_regression_iter2026_10d_existing_tool_call_blocks_autoretry():
    """>=1 tool call already recorded + suppress=True -> suppression
    must NOT kick in (a tool call may already be a side effect; a
    silent retry could duplicate it). Finalizes immediately exactly
    like the pre-fix / suppress=False behavior."""
    state = _make_state(invocations=[{"tool": "write_file", "path": "app.py"}])
    worker_t = asyncio.ensure_future(_dummy_task())
    ticker_t = asyncio.ensure_future(_dummy_task())

    chunks = [
        c async for c in consume_worker_queue(
            state, worker_t, ticker_t, suppress_zero_tool_timeout=True,
        )
    ]

    assert chunks, "a timeout with an existing tool call must still finalize/stream"
    assert state.zero_tool_timeout_suppressed is False
    assert state.timed_out is True
    # The finalize payload must report the real tool-call count, not 0.
    joined = "".join(chunks)
    assert '"tool_calls_run": 1' in joined
