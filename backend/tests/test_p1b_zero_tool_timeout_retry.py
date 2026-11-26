"""P1 L7 — zero-tool-call timeout: one bounded retry, visible, with a
clear terminal state.

Audit claim (verify-latency-debt.md, WRONG-or-STALE narrower): the
silent re-run happens ONLY for a zero-tool-call timeout, bounded to
ONE retry — but it was silent: attempt 0's ~48 s soft timeout yielded
nothing, and the turn re-ran from scratch (up to the 180 s hard
budget) with no user-visible signal — the 48 s -> 96 s+ cascade.

Fix (stream.py + watchdog.py + stream_state.py):
  * stream.py yields a visible SSE `{"type": "notice", "code":
    "provider_timeout_retry", ...}` frame before resetting for the
    single retry, and sets `state.timeout_retried = True`.
  * watchdog.py's terminal `done` payload carries
    `timeout_retry_exhausted: true` when the timeout fired on the
    retry attempt (already spent) — the terminal/paused state — and
    `false` on a first-attempt timeout.

These tests drive the REAL `consume_worker_queue` (loaded via a
synthetic package rooted at routers/chat, with the real turn.py
stubbed out — it pulls in cto_services/services.orchestrator, which
cannot install on this VM) with stubbed `services.*` helpers, and
feed it a tick past the soft deadline to fire the timeout. The
stream.py notice itself is pinned by source assertion (repo style:
the SSE-wiring pins in test_iter212m18_... do the same), because
gen() needs the full chat handler to execute.
"""
import asyncio
import json
import sys
import time
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
CHAT_DIR = BACKEND / "routers" / "chat"

# ── Load the REAL watchdog.py + stream_state.py without the real
# turn.py (heavy: cto_services.auth, services.orchestrator, ...). ──
_pkg = types.ModuleType("p1b_watchdog")
_pkg.__path__ = [str(CHAT_DIR)]
sys.modules.setdefault("p1b_watchdog", _pkg)


class _StubChatBody:
    """Minimal stand-in: only the fields watchdog.py reads."""

    def __init__(self, prompt="", session_id=None, project_id=None):
        self.prompt = prompt
        self.session_id = session_id
        self.project_id = project_id


_turn = types.ModuleType("p1b_watchdog.turn")
_turn.ChatBody = _StubChatBody
sys.modules.setdefault("p1b_watchdog.turn", _turn)

from p1b_watchdog import watchdog as wd_mod  # noqa: E402
from p1b_watchdog.stream_state import StreamState  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_services(monkeypatch):
    """consume_worker_queue imports these lazily; stub them."""
    persist_calls = []

    async def _persist_turn(*a, **k):
        persist_calls.append((a, k))

    helpers = types.ModuleType("services.chat_helpers")
    helpers._persist_turn = _persist_turn
    monkeypatch.setitem(sys.modules, "services.chat_helpers", helpers)

    contract = types.ModuleType("services.interaction_contract")
    contract.enabled = lambda: False  # ledger off: no record_* paths
    monkeypatch.setitem(sys.modules, "services.interaction_contract", contract)

    orch = types.ModuleType("services.orchestrator")
    orch._synthesise_max_iters_summary = lambda prompt, inv: "summary"
    orch.build_timeout_message = lambda n, budget, summary: (
        "The model timed out before it started.", False)
    monkeypatch.setitem(sys.modules, "services.orchestrator", orch)
    return persist_calls


def _make_state(*, soft=0.2, hard=5.0, invocations=None):
    body = _StubChatBody(prompt="do the thing", session_id="s1", project_id="p1")
    st = StreamState(request=None, body=body, authorization=None, user={},
                     user_id="u1", jwt_token="", pid_stream="test",
                     hard_timeout_s=hard, soft_timeout_s=soft)
    st.t_start = time.monotonic()
    st.activity = {"label": "thinking…", "invocations": list(invocations or [])}
    return st


async def _consume_with_tick(state, *, suppress, tick_after=0.3):
    """Run consume_worker_queue; feed one tick past the soft deadline
    so the timeout branch fires. Returns the raw SSE frames."""
    worker_t = asyncio.create_task(asyncio.sleep(3600))
    ticker_t = asyncio.create_task(asyncio.sleep(3600))

    async def feeder():
        await asyncio.sleep(tick_after)
        await state.q.put({"type": "tick", "elapsed_s": 0.3,
                           "activity": "thinking…", "invocations": [],
                           "slow_notice": False})

    feed = asyncio.create_task(feeder())
    frames = []
    try:
        async for chunk in wd_mod.consume_worker_queue(
                state, worker_t, ticker_t,
                suppress_zero_tool_timeout=suppress):
            frames.append(chunk)
    finally:
        feed.cancel()
        for t in (worker_t, ticker_t):
            t.cancel()
        await asyncio.gather(feed, worker_t, ticker_t, return_exceptions=True)
    return frames


def _payloads(frames):
    out = []
    for f in frames:
        assert f.startswith("data: "), f"not an SSE data frame: {f!r}"
        out.append(json.loads(f[len("data: "):].strip()))
    return out


def _reset_like_stream_py(state):
    """Mirror stream.py gen()'s reset between attempt 0 and 1."""
    state.timed_out = False
    state.zero_tool_timeout_suppressed = False
    state.t_start = time.monotonic()
    state.q = asyncio.Queue()
    state.stop_event = asyncio.Event()
    state.activity = {"label": "thinking…", "invocations": []}
    state.timeout_retried = True  # <-- what stream.py now does


async def test_zero_tool_soft_timeout_suppressed_attempt0(_stub_services):
    """Attempt 0 (suppress=True, 0 tool calls): the timeout is
    suppressed — NO frames yielded, nothing persisted — and the flags
    are set so the caller knows a retry is safe. Pre-existing
    behaviour, pinned."""
    persist_calls = _stub_services
    state = _make_state()
    frames = await _consume_with_tick(state, suppress=True)
    assert frames == [], f"suppressed attempt must yield nothing, got {frames}"
    assert state.zero_tool_timeout_suppressed is True
    assert state.timed_out is True
    assert persist_calls == [], "suppressed attempt must persist nothing"


async def test_second_attempt_terminal_done_carries_retry_exhausted(_stub_services):
    """The task's acceptance test: a zero-tool-call timeout retries
    ONCE, then surfaces a terminal/paused state — the `done` payload
    reports the retry was already spent instead of silently doubling."""
    persist_calls = _stub_services
    state = _make_state()
    # Attempt 0: suppressed, silent (as pinned above).
    assert await _consume_with_tick(state, suppress=True) == []
    # stream.py: visible notice + flag, then reset for attempt 1.
    _reset_like_stream_py(state)
    assert state.timeout_retried is True
    # Attempt 1: suppression OFF — the timeout finalizes normally.
    frames = await _consume_with_tick(state, suppress=False)
    payloads = _payloads(frames)
    done = [p for p in payloads if p.get("done") is True]
    assert len(done) == 1, f"expected exactly one done frame, got {payloads}"
    d = done[0]
    assert d["timed_out"] is True
    assert d["turn_state"] == "error"
    assert d["error"] == "no progress before timeout"
    assert d["timeout_retry_exhausted"] is True, (
        "terminal done must report the retry was already spent")
    assert d["failure_mode"] == "provider_rate_or_5xx"
    assert d["recovery_action"] == "auto_backoff_then_retry"
    assert len(persist_calls) == 1, "terminal timeout turn must be persisted"


async def test_first_attempt_timeout_flag_false(_stub_services):
    """Negative control: a timeout with NO retry spent reports
    `timeout_retry_exhausted: false` — the flag must not leak onto a
    first-attempt timeout."""
    state = _make_state()
    frames = await _consume_with_tick(state, suppress=False)
    done = [p for p in _payloads(frames) if p.get("done") is True]
    assert len(done) == 1
    assert done[0]["timeout_retry_exhausted"] is False
    assert done[0]["turn_state"] == "error"


async def test_tool_call_timeout_never_suppressed(_stub_services):
    """A timeout WITH tool progress is never silently retried, even on
    attempt 0: it finalizes as an explicit PARTIAL with Continue
    available. The retry stays scoped to zero-tool-call timeouts."""
    state = _make_state(invocations=["read_repo_file"])
    frames = await _consume_with_tick(state, suppress=True)
    assert state.zero_tool_timeout_suppressed is False
    done = [p for p in _payloads(frames) if p.get("done") is True]
    assert len(done) == 1
    assert done[0]["turn_state"] == "partial"
    assert done[0]["continue_available"] is True
    assert done[0]["timeout_retry_exhausted"] is False


def test_stream_state_defaults_timeout_retried_false():
    """The new StreamState field defaults to False (no retry spent)."""
    assert _make_state().timeout_retried is False


def test_stream_py_emits_visible_retry_notice():
    """Source pin (repo style): stream.py's retry loop must yield a
    user-visible notice frame and set the flag before resetting for
    attempt 1 — the silence removal itself."""
    src = (BACKEND / "routers" / "chat" / "stream.py").read_text(encoding="utf-8")
    assert '"type": "notice"' in src
    assert '"code": "provider_timeout_retry"' in src
    assert "state.timeout_retried = True" in src
    # The notice must live in the retry loop, not somewhere unrelated.
    loop_at = src.index("for _attempt in range(2):")
    assert src.index('"code": "provider_timeout_retry"') > loop_at
