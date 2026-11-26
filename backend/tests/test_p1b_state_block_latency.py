"""P1 L6 — state_block must not block the event loop on git, and the six
Mongo helpers in build_state_block must run concurrently.

Audit claim (verify-latency-debt.md, CONFIRMED, line numbers exact):
`_git_build_hash` ran a synchronous `subprocess.run(["git", ...])` on
the event loop on every turn, and `build_state_block` awaited six
helpers one-by-one.

Fix: the git hash is resolved once per process via `asyncio.to_thread`
and cached (`_cached_build_hash`); the six helpers run under a single
`asyncio.gather`.

The fake db below mimics Motor's awaitables (async methods, async
cursors). `services.journey_watch` is stubbed in sys.modules because
`_funnel_7d` imports it lazily; the stub sleeps like a real query so
the batching assertion is meaningful.
"""
import asyncio
import sys
import threading
import time
import types

import pytest

from services.ora_chat_v2 import state_block


# ── Fake Motor-shaped db ─────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows, delay=0.0):
        self._rows = rows
        self._delay = delay

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        for row in self._rows:
            yield row


class _FakeCollection:
    def __init__(self, rows=None, delay=0.0):
        self._rows = rows or []
        self._delay = delay

    async def count_documents(self, *a, **k):
        await asyncio.sleep(self._delay)
        return len(self._rows)

    def find(self, *a, **k):
        return _FakeCursor(self._rows, delay=self._delay)


class _FakeDB:
    """Each collection sleeps `delay` per query so sequential vs
    gathered execution is measurable in wall time."""

    def __init__(self, delay=0.2):
        self._delay = delay
        self.health_notifications = _FakeCollection(
            [{"category": "deploy"}, {"category": "deploy"}], delay=delay)
        self.github_installations = _FakeCollection([{}, {}], delay=delay)
        self.ora_backlog_items = _FakeCollection(
            [{"backlog_id": "B1", "title": "t", "status": "parked", "note": ""}],
            delay=delay)
        self.loop_sessions = _FakeCollection(
            [{"error_code": "E_TIMEOUT", "updated_at": time.time()}], delay=delay)

    async def command(self, *a, **k):
        await asyncio.sleep(self._delay)
        return {"ok": 1}


@pytest.fixture(autouse=True)
def _stub_journey_watch(monkeypatch):
    """_funnel_7d imports services.journey_watch lazily; give it a
    stub that sleeps like a real query and returns a canned card."""
    mod = types.ModuleType("services.journey_watch")

    async def compute_journey_watch_card(db, period_days=7):
        await asyncio.sleep(0.2)
        return {"stalls_flagged": 1, "stalls_resolved": 0, "hardbreaks": 0,
                "active_stalls": 1, "by_stage": []}

    mod.compute_journey_watch_card = compute_journey_watch_card
    monkeypatch.setitem(sys.modules, "services.journey_watch", mod)
    # Each test starts with a cold hash cache so the git behaviour is
    # measured, not inherited from a previous test. raising=False so
    # the fixture also works on a pre-fix tree (no cache attr yet).
    monkeypatch.setattr(state_block, "_BUILD_HASH_CACHE", None, raising=False)
    yield


async def test_git_hash_runs_off_event_loop(monkeypatch):
    """A slow synchronous git subprocess must NOT stall the loop: a
    heartbeat coroutine keeps ticking while build_state_block runs."""
    main_thread = threading.get_ident()
    git_threads = []

    def slow_git():
        git_threads.append(threading.get_ident())
        time.sleep(0.5)  # synchronous block, like subprocess.run
        return "abc1234"

    monkeypatch.setattr(state_block, "_git_build_hash", slow_git)
    beats = []

    async def heartbeat():
        while True:
            beats.append(time.monotonic())
            await asyncio.sleep(0.01)

    hb = asyncio.create_task(heartbeat())
    try:
        out = await state_block.build_state_block(_FakeDB(delay=0.0))
    finally:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass

    assert "build_hash=abc1234" in out
    assert git_threads and git_threads[0] != main_thread, (
        "git hash must resolve on a worker thread, not the loop thread")
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert gaps, "heartbeat never ticked"
    assert max(gaps) < 0.25, (
        f"event loop stalled for {max(gaps):.2f}s during the git call "
        f"(>0.25s proves the sync call blocked the loop)")


async def test_heartbeat_control_detects_a_blocking_call():
    """Negative control for the test above: the SAME heartbeat harness
    must report a stall when a slow sync call really does run on the
    loop thread — proving the test can tell blocked from unblocked."""
    beats = []

    async def heartbeat():
        while True:
            beats.append(time.monotonic())
            await asyncio.sleep(0.01)

    hb = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.05)  # let the heartbeat record a beat first
        time.sleep(0.5)  # deliberate blocking call on the loop thread
        # Give the heartbeat a real window (several 0.01 s ticks) to
        # record a post-block beat: `await asyncio.sleep(0)` alone
        # yields only once, and the test coroutine can resume + cancel
        # the heartbeat before it ever runs again, hiding the stall.
        await asyncio.sleep(0.05)
    finally:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert gaps and max(gaps) >= 0.4, (
        f"control failed: heartbeat saw max gap {max(gaps):.2f}s, "
        "expected >= 0.4s for a 0.5s blocking call")


async def test_six_helpers_run_batched():
    """Six helpers x 0.2s each: sequential would take >= 1.2s;
    gathered takes ~0.4s (the slowest helper does two 0.2s queries).
    Also asserts the output still carries all six sections."""
    db = _FakeDB(delay=0.2)
    t0 = time.monotonic()
    out = await state_block.build_state_block(db)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.8, (
        f"build_state_block took {elapsed:.2f}s — the six helpers "
        "look sequential again (gathered should be < 0.8s)")
    for section in ("production: build_hash=", "funnel_7d:",
                    "alerts_7d: total=2", "open_blockers:",
                    "parked_backlog:", "recent_loop_failures_7d: count=1"):
        assert section in out, f"missing section {section!r} in:\n{out}"


async def test_build_hash_resolved_once_per_process(monkeypatch):
    """The hash is cached: two full turns cost exactly one git call."""
    calls = []

    def counting_git():
        calls.append(1)
        return "deadbee"

    monkeypatch.setattr(state_block, "_git_build_hash", counting_git)
    db = _FakeDB(delay=0.0)
    out1 = await state_block.build_state_block(db)
    out2 = await state_block.build_state_block(db)
    assert len(calls) == 1, f"git ran {len(calls)}x for 2 turns, expected 1 (cached)"
    assert "build_hash=deadbee" in out1 and "build_hash=deadbee" in out2
