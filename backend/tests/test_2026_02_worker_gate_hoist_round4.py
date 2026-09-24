"""
tests/test_2026_02_worker_gate_hoist_round4.py — Part B3 round-4
regression.

Testing round 3 found the gate was FLAKY (~1/3 hit rate) on
/chat/stream because `state.required_read_gate` used to be set only
deep inside run_worker()'s orchestrator branch (_prep_pre_llm), AFTER
five earlier mode-dispatch short-circuits (Mode D fast-path, D/E,
B, F, ORA-panel cascade) that can each intercept the turn and `return`
before ever reaching that line. The exact incident message ("Fix this
critical issue: exec_usage (in <path>)") is ambiguous enough to
sometimes land in Mode D's debug session instead of the general
orchestrator path — bypassing the gate entirely in those runs.

Fix: `state.required_read_gate` is now computed at the very TOP of
run_worker(), before any mode branch executes, so it's populated no
matter which branch produces the final result. This test proves the
hoist by forcing an EARLY mode branch to short-circuit (simulating the
Mode D bypass) and asserting the gate is already set by the time that
happens.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from routers.chat.stream_state import StreamState
from routers.chat.turn import ChatBody

REAL_PATH = (
    ".agent/skills/plugins/antigravity-awesome-skills-claude/"
    "skills/007/scripts/scanners/injection_scanner.py"
)
INCIDENT_MESSAGE = f"Fix this critical issue: exec_usage (in {REAL_PATH})"


def _make_state(prompt: str) -> StreamState:
    body = ChatBody(prompt=prompt, session_id="test-session-round4")
    return StreamState(
        request=None, body=body, authorization=None, user={},
        user_id="test_user_round4", jwt_token="", pid_stream="p1",
    )


def test_required_read_gate_is_set_before_mode_d_can_bypass_it():
    """Simulates the confirmed live bypass: Mode D's debug-session
    branch (_mode_d_e) short-circuits with a result BEFORE the
    orchestrator branch's own gate computation would ever run. The
    hoisted gate at the top of run_worker() must already be populated
    by the time that short-circuit fires."""
    from routers.chat import worker as worker_mod

    state = _make_state(INCIDENT_MESSAGE)

    async def fake_read_repo_file(ctx, args):
        return {"ok": False, "error": "No project selected."}

    async def bypass_result(*_a, **_kw):
        # Mode D "intercepts" the turn and returns a result WITHOUT
        # ever calling _prep_pre_llm — this is the exact confirmed
        # live bypass path (mode_d_debugger's own reply, unrelated to
        # required_tool_gate.py).
        return {"ok": True, "content": "mode-d bypass reply", "provider": "mode-d-debugger"}

    with patch("services.local_tools.read_repo_file", new=AsyncMock(side_effect=fake_read_repo_file)), \
         patch.object(worker_mod, "_mode_d_fast_path", new=AsyncMock(return_value=None)), \
         patch.object(worker_mod, "_mode_broadcast", new=AsyncMock(return_value="D")), \
         patch.object(worker_mod, "_mode_d_e", new=AsyncMock(side_effect=bypass_result)):
        asyncio.get_event_loop().run_until_complete(worker_mod.run_worker(state))

    # run_worker() reports its result via state.q (consumed by
    # stream.py's consume_worker_queue in the real pipeline), not
    # state.result directly — drain it here.
    queued = asyncio.get_event_loop().run_until_complete(state.q.get())
    assert queued["result"] == {"ok": True, "content": "mode-d bypass reply", "provider": "mode-d-debugger"}
    # ...but the gate was STILL populated, because it's computed
    # before _mode_d_e ever runs — proving stream.py's later
    # enforce_required_read_gate() call has real gate data to work
    # with regardless of which mode branch answered.
    assert state.required_read_gate is not None
    assert state.required_read_gate["hit"]["rule_id"].lower() == "exec_usage"
    assert state.required_read_gate["hit"]["path"] == REAL_PATH


def test_required_read_gate_is_none_for_an_unrelated_message():
    from routers.chat import worker as worker_mod

    state = _make_state("please make the button blue")

    async def bypass_result(*_a, **_kw):
        return {"ok": True, "content": "sure, made it blue", "provider": "mode-b"}

    with patch.object(worker_mod, "_mode_d_fast_path", new=AsyncMock(return_value=None)), \
         patch.object(worker_mod, "_mode_broadcast", new=AsyncMock(return_value="B")), \
         patch.object(worker_mod, "_mode_d_e", new=AsyncMock(return_value=None)), \
         patch.object(worker_mod, "_mode_b", new=AsyncMock(side_effect=bypass_result)):
        asyncio.get_event_loop().run_until_complete(worker_mod.run_worker(state))

    queued = asyncio.get_event_loop().run_until_complete(state.q.get())
    assert queued["result"] == {"ok": True, "content": "sure, made it blue", "provider": "mode-b"}
    assert state.required_read_gate is None
