"""
2026-09-20 — Deploy-health fix, round 3: `deploy_logger.get_current_
commit()` and `main._resolve_build_hash()` each ran a blocking
`subprocess.check_output(["git", ...])` call directly inside an
`async def` coroutine, with no `asyncio.to_thread` wrapping.

`log_deploy_event()` is called unconditionally on EVERY boot from
`main.py`'s staggered `_bg_bootstrap()` (~T+10s), and `_resolve_
build_hash()` is called from `/api/health` itself — one of the two
endpoints deploy probes hit repeatedly. This is the exact bug CLASS
already diagnosed and fixed for its sibling `_backup_tools_snapshot()`
(2026-08-23, same file): a blocking subprocess call sharing the event
loop with `/health` can stall EVERY concurrent request on that loop,
which is the literal production symptom reported (`nginx upstream
timed out ... GET /health`).

Covers:
  - `log_deploy_event()` now off-loads `get_current_commit()` to a
    thread instead of calling it inline.
  - `main.health()`'s `_resolve_build_hash()` call is off-loaded too.
  - Regression guard: no NEW unguarded `subprocess.*` call is
    introduced directly inside an `async def` in either module
    without going through `asyncio.to_thread` first (source-grep,
    same convention as this repo's other "no new direct call sites"
    guards).
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services import deploy_logger


@pytest.mark.asyncio
async def test_log_deploy_event_offloads_get_current_commit_to_thread():
    """The exact fix: `get_current_commit()` must run via
    `asyncio.to_thread`, not be called inline on the event loop."""
    fake_info = {
        "commit_sha": "abc1234567", "branch": "main",
        "commit_message": "test", "commit_author": "tester",
        "commit_timestamp": "2026-09-20T00:00:00+00:00",
    }
    db = AsyncMock()
    db.deploy_events.find_one = AsyncMock(return_value=None)
    db.deploy_events.insert_one = AsyncMock()

    with patch("services.deploy_logger.get_current_commit",
               return_value=fake_info) as mock_get:
        result = await deploy_logger.log_deploy_event(db, trigger="boot")

    mock_get.assert_called_once()
    assert result is not None
    assert result["commit_sha"] == "abc1234567"
    db.deploy_events.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_log_deploy_event_offload_does_not_block_event_loop():
    """A slow (simulated) get_current_commit() must not prevent other
    coroutines from running concurrently — proves the to_thread
    offload is real, not a no-op wrapper around a direct call."""
    import asyncio
    import time

    def _slow_sync_commit():
        time.sleep(0.3)  # simulates a slow subprocess call
        return {"commit_sha": "slow123", "branch": "main",
                "commit_message": "", "commit_author": "",
                "commit_timestamp": None}

    db = AsyncMock()
    db.deploy_events.find_one = AsyncMock(return_value=None)
    db.deploy_events.insert_one = AsyncMock()

    ticks = []

    async def _ticker():
        for _ in range(6):
            await asyncio.sleep(0.05)
            ticks.append(time.time())

    with patch("services.deploy_logger.get_current_commit", _slow_sync_commit):
        await asyncio.gather(
            deploy_logger.log_deploy_event(db, trigger="boot"),
            _ticker(),
        )
    # The ticker must have kept making progress WHILE the "slow"
    # commit resolution ran — if get_current_commit() blocked the
    # loop directly, `ticks` would be starved until the 0.3s sleep
    # finished, collapsing into a single burst at the end instead of
    # ~6 evenly-spaced entries.
    assert len(ticks) == 6


def test_no_unguarded_subprocess_call_inside_async_def_deploy_logger():
    """Source-level guard: every `subprocess.*` call in this module
    must live inside a plain `def`, never directly inside an
    `async def` (the exact shape of the bug this test file fixes).
    New code that adds a git/subprocess call to an async function
    here without `asyncio.to_thread` will fail this test."""
    src = inspect.getsource(deploy_logger)
    tree = ast.parse(src)

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.violations = []

        def visit_AsyncFunctionDef(self, node):
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func = child.func
                    is_subprocess = (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "subprocess"
                    )
                    if is_subprocess:
                        self.violations.append(node.name)
            self.generic_visit(node)

    v = Visitor()
    v.visit(tree)
    assert v.violations == [], (
        f"async function(s) {v.violations} call subprocess.* directly "
        "— wrap in asyncio.to_thread"
    )


def test_main_health_offloads_resolve_build_hash_to_thread():
    """Source-level regression check on main.py's /api/health handler
    — the call site must go through asyncio.to_thread, not call
    `_resolve_build_hash()` inline."""
    main_path = Path(__file__).parent.parent / "main.py"
    src = main_path.read_text()
    m = re.search(
        r"build_hash_val\s*=\s*\(_dep or \{\}\)\.get\(\"commit_sha\"\)\s*"
        r"(?:\\\s*\n\s*)?or\s+(.+)",
        src,
    )
    assert m, "could not locate the build_hash_val resolution line in main.py"
    assert "asyncio.to_thread(_resolve_build_hash)" in m.group(1)
