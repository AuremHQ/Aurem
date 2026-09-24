"""
2026-09-18 — Deployment fix, round 2: nginx `/health` STILL timed out
4x across ~20s on a real prod boot, even after the round-1 fix
(create_task-wrapping the maintenance/G19/G6 chain + GitHub App
config hydration so neither blocks uvicorn's port bind).

Root cause this round: create_task-wrapping only fixes "does this
block the PORT BIND" — it does NOT fix "does this compete for CPU
during the readiness-probe window". ~8 background boot tasks (the
maintenance/G19/G6 chain, loop_safety indexes, fix_jobs orphan sweep,
github_app indexes, ora_learning indexes, visual_bugfix_storage
warm-up — which makes a REAL outbound HTTP call — and 3 bare
health-score index creates) all fired with literally ZERO delay,
piling every one of their Mongo round-trips onto the exact same
instant. Under this deploy's documented "prod 500m CPU cap" (see
`_warm_codebase_index`'s existing Iter-336 comment), that's enough
concurrent CPU/IO demand to starve the event loop for multi-second
stretches — long enough that nginx's upstream read on a periodic
`/health` probe times out, even though uvicorn already has the port
bound and `/health` itself is a microsecond handler.

Fix: stagger these boot tasks (2s/4s/6s/8s/9s/11s/14s) — the exact
same pattern this file already uses successfully at 30s/45s/60s/75s
for the lower-priority ones (dev_users backfills, Iter 272 indexes,
codebase index warm). Spreads the burst instead of leaving it all at
T+0, without changing any of round 1's non-blocking guarantees.
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


def _body_of(block: str, def_line: str, next_marker: str) -> str:
    """Slice out one nested `async def`'s body by its def line and the
    next distinctive marker after it (same slicing convention used by
    test_bug1_duplicate_prs_round2_2026_09_18.py's loop_engine checks)."""
    assert def_line in block, f"could not find {def_line!r} in lifespan block"
    return block.split(def_line, 1)[1].split(next_marker, 1)[0]


def test_boot_maintenance_g19_g6_is_staggered():
    block = _lifespan_block()
    body = _body_of(block, "async def _boot_maintenance_g19_g6():",
                     "app.state.loop_housekeeping_task = _supervise(")
    assert "await _asyncio.sleep(2)" in body


def test_ensure_loop_safety_indexes_is_staggered():
    block = _lifespan_block()
    body = _body_of(block, "async def _ensure_loop_safety_indexes():",
                     "_asyncio.create_task(_ensure_loop_safety_indexes())")
    assert "await _asyncio.sleep(4)" in body


def test_orphan_running_fix_jobs_is_staggered():
    block = _lifespan_block()
    body = _body_of(block, "async def _orphan_running_fix_jobs():",
                     "_asyncio.create_task(_orphan_running_fix_jobs())")
    assert "await _asyncio.sleep(6)" in body


def test_github_app_indexes_is_staggered():
    block = _lifespan_block()
    body = _body_of(block, "async def _github_app_indexes():",
                     "_asyncio.create_task(_github_app_indexes())")
    assert "await _asyncio.sleep(8)" in body


def test_ensure_ora_learning_indexes_is_staggered():
    block = _lifespan_block()
    body = _body_of(block, "async def _ensure_ora_learning_indexes():",
                     "_asyncio.create_task(_ensure_ora_learning_indexes())")
    assert "await _asyncio.sleep(11)" in body


def test_warm_visual_bugfix_storage_is_staggered():
    block = _lifespan_block()
    body = _body_of(block, "async def _warm_visual_bugfix_storage():",
                     "_asyncio.create_task(_warm_visual_bugfix_storage())")
    assert "await _asyncio.sleep(14)" in body


def test_health_score_indexes_are_staggered_and_still_wrapped_in_task():
    block = _lifespan_block()
    body = _body_of(block, "async def _ensure_health_score_indexes():",
                     "_asyncio.create_task(_ensure_health_score_indexes())")
    assert "await _asyncio.sleep(9)" in body
    assert "health_endpoint_latency.create_index" in body
    assert "health_test_coverage_runs.create_index" in body
    assert "architecture_review_log.create_index" in body


def test_github_app_config_hydration_still_fires_near_instantly():
    """The ONE boot task deliberately left un-staggered — it's the
    warm-path for real user-facing chat requests against a
    github_app-connected repo in the first seconds after boot (see
    its own docstring: 'keeps the warm within ~1s intent'). Confirm
    this round's stagger fix didn't accidentally slow it down too."""
    block = _lifespan_block()
    body = _body_of(block, "async def _hydrate_github_app_config():",
                     "_asyncio.create_task(_hydrate_github_app_config())")
    assert "await _asyncio.sleep" not in body
