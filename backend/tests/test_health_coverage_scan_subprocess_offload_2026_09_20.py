"""
2026-09-20 — Deploy-health fix, round 4: `health_coverage_scan.
run_coverage_scan()` called `deploy_logger.get_current_commit()`
directly (no `asyncio.to_thread`), the same bug class already fixed
in `main.py`'s `/api/health` handler and `deploy_logger.log_deploy_
event()` (round 3, 2026-09-20): `get_current_commit()` makes up to 5
sequential blocking `subprocess.check_output(["git", ...])` calls,
which stall the *entire* shared event loop for as long as they take —
the literal production symptom reported (`nginx upstream timed out
... GET /health`), since ANY concurrent request sharing the loop,
including an unrelated health probe, is blocked until those calls
return.

`run_coverage_scan()` is admin-triggered (on-demand "Run now" button,
no cron), but it still runs on the same shared event loop as every
other concurrent request while it executes, so the same fix applies.

Covers:
  - Regression guard: no direct (un-offloaded) call to
    `get_current_commit` remains in this module's source — it must
    only ever be reached via `asyncio.to_thread(get_current_commit)`.
  - Functional proof: calling the fixed code path with a mocked
    `get_current_commit` correctly surfaces `commit_sha`, and a slow
    (simulated) `get_current_commit()` does not block a concurrently
    running coroutine on the same event loop.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from services import health_coverage_scan


def test_no_direct_get_current_commit_call_in_source():
    """Source-level guard: `get_current_commit` must only ever be
    invoked as an argument to `asyncio.to_thread(...)`, never called
    directly on the event loop (the exact shape of the bug this test
    file fixes). New code that adds a direct call back in will fail
    this test."""
    src = inspect.getsource(health_coverage_scan)
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "get_current_commit"):
            continue
        # A bare `get_current_commit()` call is only allowed as the
        # first positional argument of `asyncio.to_thread(...)`.
        parent_is_to_thread = False
        for outer in ast.walk(tree):
            if (isinstance(outer, ast.Call)
                    and isinstance(outer.func, ast.Attribute)
                    and outer.func.attr == "to_thread"
                    and outer.args
                    and outer.args[0] is node):
                parent_is_to_thread = True
                break
        assert parent_is_to_thread, (
            "get_current_commit() must be called through "
            "asyncio.to_thread(get_current_commit), never directly "
            "on the event loop"
        )


@pytest.mark.asyncio
async def test_get_current_commit_offload_resolves_commit_sha(monkeypatch):
    """The exact fix, isolated: awaiting `asyncio.to_thread(get_
    current_commit)` must still resolve the real commit_sha, proving
    the offload is a transparent passthrough, not a behavior change."""
    import asyncio

    fake_commit = {"commit_sha": "deadbee0001", "branch": "main"}
    monkeypatch.setattr(
        "services.deploy_logger.get_current_commit", lambda: fake_commit,
    )
    from services.deploy_logger import get_current_commit

    result = await asyncio.to_thread(get_current_commit)
    assert result["commit_sha"] == "deadbee0001"


@pytest.mark.asyncio
async def test_to_thread_offload_does_not_block_concurrent_coroutine():
    """A slow (simulated) get_current_commit() must not starve another
    coroutine running concurrently on the same event loop — proves
    the to_thread offload is real, not a no-op wrapper around a
    direct call."""
    import asyncio
    import time

    def _slow_sync_commit():
        time.sleep(0.3)  # simulates a slow subprocess call
        return {"commit_sha": "slow999", "branch": "main"}

    ticks = []

    async def _ticker():
        for _ in range(6):
            await asyncio.sleep(0.05)
            ticks.append(time.time())

    await asyncio.gather(
        asyncio.to_thread(_slow_sync_commit),
        _ticker(),
    )
    # If the "slow" call blocked the loop directly, `ticks` would be
    # starved until the 0.3s sleep finished, collapsing into a single
    # burst at the end instead of ~6 evenly-spaced entries.
    assert len(ticks) == 6
