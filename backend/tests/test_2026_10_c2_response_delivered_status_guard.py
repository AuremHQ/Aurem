"""test_2026_10_c2_response_delivered_status_guard.py — C2 fix, part c.

Founder: "a turn that promises an action and runs none must not show
'Response delivered'." `interaction_contract.finalize()` previously
only caught this via a text-phrase heuristic
(`_is_intent_only_announcement`, e.g. "Let me check..."). A reply that
still carries a literal unexecuted tool-call tag (LongCat's own
`<longcat_tool_call>` or the generic `<tool_call>`/`<function_call>`)
with zero real tool_calls_run is the SAME bug, caught structurally
here as defense in depth (the LongCat-specific leak itself is already
prevented earlier, in `_call_longcat`'s GLM fallback)."""
from __future__ import annotations

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.interaction_contract import finalize  # noqa: E402


@pytest.fixture(autouse=True)
def _interaction_contract_on(monkeypatch):
    # Set per test, not at import: a module-level os.environ write stays
    # on for the whole pytest process and flips behaviour in every test
    # collected after this file (it broke collection of
    # test_iter138_acceptance_seven.py, which aborted the live-env job).
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")


def test_leaked_longcat_tag_with_zero_tool_calls_is_never_success():
    content = (
        "Let me check that file.\n"
        "<longcat_tool_call>read_repo_file {\"path\":\"backend/main.py\"}</longcat_tool_call>"
    )
    result = {"ok": True, "tool_calls_run": 0}
    out = finalize(result, content, prompt="read main.py", session_id="s1", request_id="r1")
    assert out["turn_state"] != "success"
    assert out["completion_basis"] != "response_delivered"


def test_generic_tool_call_tag_with_zero_tool_calls_is_never_success():
    content = "Sure.\n<tool_call>{\"name\": \"list_repo_files\", \"arguments\": {}}</tool_call>"
    result = {"ok": True, "tool_calls_run": 0}
    out = finalize(result, content, prompt="list files", session_id="s1", request_id="r2")
    assert out["turn_state"] != "success"


def test_tag_present_but_a_real_tool_did_run_is_not_forced_partial():
    """A tag alongside a GENUINE tool_calls_run>0 is a different bug
    (stray leftover text next to a real action) — not this guard's
    job to flag; it would otherwise false-positive on legitimate
    successful turns that happen to echo tag-like text."""
    content = "Done — <tool_call> was just stray echo text, ignore it."
    result = {"ok": True, "tool_calls_run": 1}
    out = finalize(result, content, prompt="fix it", session_id="s1", request_id="r3")
    assert out["turn_state"] == "success"


def test_normal_reply_with_no_tag_and_zero_tool_calls_is_still_success():
    content = "No open findings right now — you're all caught up."
    result = {"ok": True, "tool_calls_run": 0}
    out = finalize(result, content, prompt="?", session_id="s1", request_id="r4")
    assert out["turn_state"] == "success"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
