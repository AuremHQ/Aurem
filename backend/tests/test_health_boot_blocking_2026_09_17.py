"""
2026-09-17 — Deployment fix: nginx "upstream timed out reading
response header for /health" during boot on production (Emergent
deploy, Atlas-hosted Mongo).

Root cause: `lifespan()` awaited a chain of Mongo-dependent boot work
directly, BEFORE `yield` — maintenance boot-gap detection
(`load_maintenance_state` / `read_last_heartbeat` / `write_heartbeat`),
G19 `record_boot`, G6 `ensure_dedup_indexes`, and (separately) the
GitHub App config hydration `find_one`. Motor's client connects
LAZILY, so on a genuinely cold Atlas connection (a fresh deploy pod)
the FIRST of these calls pays the full TLS+SRV+discovery cost —
uvicorn does not bind port 8001 until everything before `yield`
returns, so nginx's readiness probe times out. This is the exact
same regression class the original Iter 127 fix
(`test_iter127_lifespan_nonblocking.py`) already guards against for
the mongo ping / index-ensure / deploy-log trio; this test extends
that guard to the two call sites this round of debugging found.

The fix wraps both chains in their own nested `async def` helper and
schedules them with `_asyncio.create_task(...)` instead of awaiting
them directly at `lifespan()`'s own top-level indentation. So the
correctness check here isn't "does this text appear before some
other function is defined" (that boundary is arbitrary — other
non-blocking helpers get defined and scheduled throughout the whole
pre-yield body) — it's "is this await nested INSIDE a helper that is
itself only ever scheduled via `create_task`, never `await`ed
directly at lifespan's own indentation".
"""
from __future__ import annotations

import pathlib
import re


MAIN_PY = pathlib.Path(__file__).resolve().parents[1] / "main.py"


def _lifespan_block() -> str:
    src = MAIN_PY.read_text(encoding="utf-8")
    m = re.search(
        r"@asynccontextmanager\s*\nasync def lifespan\(.*?\n(    if.*?bootstrap_task.*?\.cancel\(\))",
        src,
        re.DOTALL,
    )
    assert m, "could not isolate lifespan() block from main.py"
    return m.group(0)


def _indent_of(block: str, needle: str) -> int:
    """Leading-whitespace width of the line containing `needle`."""
    for line in block.splitlines():
        if needle in line:
            return len(line) - len(line.lstrip(" "))
    raise AssertionError(f"could not find {needle!r} in lifespan block")


def _assert_nested_and_task_scheduled(block: str, blocking_line: str, helper_name: str) -> None:
    """The blocking line must be indented deeper than lifespan's own
    top-level statements (i.e. nested inside a helper `async def`),
    and that helper must be scheduled via `create_task`, never
    `await`ed directly."""
    lifespan_top_indent = _indent_of(block, "async def lifespan(") + 4
    actual_indent = _indent_of(block, blocking_line)
    assert actual_indent > lifespan_top_indent, (
        f"`{blocking_line}` is awaited at lifespan's own top-level "
        f"indentation (not nested inside a background helper) — on a "
        f"cold Atlas connection this blocks uvicorn's port bind and "
        f"reproduces the nginx /health readiness-probe timeout."
    )
    assert f"_asyncio.create_task({helper_name}())" in block, (
        f"`{helper_name}` is no longer scheduled via create_task — "
        f"either it's missing entirely (the work it does would never "
        f"run) or it's awaited directly, which would reintroduce the "
        f"boot-blocking bug."
    )
    assert f"await {helper_name}()" not in block, (
        f"`{helper_name}` is awaited directly somewhere in lifespan — "
        f"that defeats the whole point of scheduling it as a "
        f"background task."
    )


def test_maintenance_g19_g6_boot_chain_not_awaited_inline() -> None:
    """The maintenance-gap-detect -> G19 record_boot -> G6 dedup-index
    chain must run inside a task scheduled via `create_task`, not be
    awaited directly in lifespan's own body."""
    block = _lifespan_block()
    blockers = [
        "_maint_state = await _maint_load(app.state.db)",
        "_g19_boot = await _g19_record(app.state.db)",
        "_g6_res = await ensure_dedup_indexes(app.state.db)",
    ]
    for blocker in blockers:
        _assert_nested_and_task_scheduled(block, blocker, "_boot_maintenance_g19_g6")


def test_github_app_config_hydration_not_awaited_inline() -> None:
    """The GitHub App config `find_one` must not be awaited directly
    in lifespan's own body either — same cold-Mongo blocking risk."""
    block = _lifespan_block()
    _assert_nested_and_task_scheduled(
        block,
        "gh_row = await app.state.db.admin_settings.find_one(",
        "_hydrate_github_app_config",
    )
