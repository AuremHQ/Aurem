"""
2026-09-21 — deploy-health fix, round 7: live-reproduced regression.
Production logs showed nginx "upstream timed out ... GET /health"
bursts + "client disconnected mid-response ... No response returned"
errors happening ~30s after a fresh boot — NOT during a restart loop
this time (only one boot recorded), during otherwise-normal request
serving.

Root-caused via `/var/log/supervisor/backend.err.log` history: the
boot's background warm-up task (`main.py::_warm_codebase_index`,
deferred 45s specifically to protect the post-deploy readiness
window — Iter 336) unconditionally called
`codebase_index.build_index(force=True)`, a CPU-heavy walk of the
whole repo (500-1500 files). On this specific boot, a REAL user's
first ORA chat message ALSO triggered the exact same walk via
`_ensure_fresh()` just 7s after boot (there's no equivalent stagger
protection on that path — it can't know when a human will type).
Since `build_index(force=True)` always ignores the 15-minute TTL
cache regardless of how fresh it already is, the deferred task then
redundantly repeated the SAME expensive walk ~25s later even though
the cache was still fresh — a second CPU-heavy walk stacked directly
on top of the first, and that second walk's ~1.1s window lines up
exactly with the observed nginx timeout + disconnect burst.

Fix: `main.py`'s deferred warm-up now calls `build_index()` with its
default `force=False`, which already has the identical TTL check —
making it a fast no-op whenever a real request warmed the cache
first, instead of an unconditional second CPU-heavy walk.
"""
from __future__ import annotations

import time

import pytest

from services.ora_chat import codebase_index as cb


@pytest.mark.asyncio
async def test_build_index_default_force_skips_when_already_warm(monkeypatch):
    """The exact fix: build_index() (no force arg) must NOT re-walk
    the repo if the cache is already fresh — only build_index(force=
    True) may unconditionally do that."""
    walk_calls = {"count": 0}
    real_walk = cb._walk_scan_dirs

    def _counting_walk():
        walk_calls["count"] += 1
        return real_walk()

    monkeypatch.setattr(cb, "_walk_scan_dirs", _counting_walk)

    r1 = await cb.build_index(force=True)
    assert r1["cached"] is False
    assert walk_calls["count"] == 1

    # Second call, default force=False, cache is fresh (built moments
    # ago) — must be a no-op, NOT a second walk.
    r2 = await cb.build_index()
    assert r2["cached"] is True
    assert walk_calls["count"] == 1, (
        "build_index() with default force=False must not re-walk "
        "the repo when the 15-min TTL cache is still fresh"
    )


@pytest.mark.asyncio
async def test_build_index_force_true_always_rewalks(monkeypatch):
    """Sanity check the inverse: force=True is still available for
    callers that genuinely need an unconditional rebuild (e.g. an
    admin "refresh index" action), and IS expected to re-walk even
    when fresh."""
    walk_calls = {"count": 0}
    real_walk = cb._walk_scan_dirs

    def _counting_walk():
        walk_calls["count"] += 1
        return real_walk()

    monkeypatch.setattr(cb, "_walk_scan_dirs", _counting_walk)

    await cb.build_index(force=True)
    await cb.build_index(force=True)
    assert walk_calls["count"] == 2


@pytest.mark.asyncio
async def test_build_index_rewalks_after_ttl_expiry(monkeypatch):
    """Sanity check the TTL boundary itself still works after the
    fix — a genuinely stale cache must still be rebuilt by the
    default force=False path (this is what keeps the index from
    going permanently stale once the redundant double-walk is gone)."""
    walk_calls = {"count": 0}
    real_walk = cb._walk_scan_dirs

    def _counting_walk():
        walk_calls["count"] += 1
        return real_walk()

    monkeypatch.setattr(cb, "_walk_scan_dirs", _counting_walk)

    await cb.build_index(force=True)
    assert walk_calls["count"] == 1

    # Simulate TTL expiry by rewinding the cached build timestamp.
    cb._CACHE["built_at"] = time.time() - cb._INDEX_TTL_S - 5

    r2 = await cb.build_index()
    assert r2["cached"] is False
    assert walk_calls["count"] == 2
