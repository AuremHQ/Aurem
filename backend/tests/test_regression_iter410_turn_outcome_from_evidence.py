"""
A turn's outcome is decided by evidence (tool results, stored/executed
change), not by how the reply sounds. Found by the ORA chat reliability
replay: 10 of 11 turns were labelled "Response delivered" with zero work.
"""
import pytest

from services.interaction_contract import finalize
from services.turn_outcome import DONE, FAILED, NEEDS_YOU, NOTHING_DONE, decide


def _env(result, content, monkeypatch, state="success"):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    return finalize(dict(result, turn_state=state), content, prompt="p", session_id="s", request_id="r")


def test_regression_iter410_reply_without_tool_result_is_not_done(monkeypatch):
    env = _env({}, "Clean bill of health on the scans.", monkeypatch)
    assert env["outcome"] == NOTHING_DONE and env["reason"] == "answered"
    assert env["completion_basis"] != "response_delivered"


@pytest.mark.parametrize("text", [
    "Let's finish the Scaling section and then I'll submit the full draft. (Standby — building that now...)",
    "I'll create the file with core guidelines. One moment while I draft it.",
])
def test_regression_iter410_promise_with_no_tool_result_is_announced_not_done(text, monkeypatch):
    env = _env({"tool_calls_run": 0, "tool_invocations": []}, text, monkeypatch)
    assert (env["outcome"], env["reason"]) == (NOTHING_DONE, "announced_not_done")


def test_regression_iter410_promise_backed_by_a_real_tool_result_is_done(monkeypatch):
    inv = [{"tool": "write_repo_file", "ok": True}]
    env = _env({"tool_invocations": inv}, "The file docs/OPERATIONS.md was written and committed to the branch.", monkeypatch)
    assert env["outcome"] == DONE and env["evidence"] == ["write_repo_file"]


def test_regression_iter410_failed_or_forced_tool_calls_are_not_evidence(monkeypatch):
    inv = [{"tool": "read_repo_file", "ok": False}, {"tool": "fetch_url", "ok": True, "forced": True}]
    env = _env({"tool_invocations": inv}, "Here is what I found.", monkeypatch)
    assert env["outcome"] == NOTHING_DONE


def test_regression_iter410_pending_change_and_executor_providers(monkeypatch):
    assert _env({}, "ok\n```aurem-handoff\nx\n```", monkeypatch)["outcome"] == NEEDS_YOU
    assert _env({"provider": "commit-boundary-executor"}, "Done, PR opened.", monkeypatch)["outcome"] == DONE
    none = _env({"provider": "commit-boundary-no-pending"}, "I don't have a change waiting.", monkeypatch)
    assert (none["outcome"], none["reason"]) == (NOTHING_DONE, "nothing_pending")


def test_regression_iter410_error_and_blocked_states():
    assert decide({}, "", "error")["outcome"] == FAILED
    assert decide({}, "x", "blocked")["outcome"] == NEEDS_YOU


def test_regression_iter410_turn_state_is_unchanged_for_old_consumers(monkeypatch):
    env = _env({}, "Delivered factual answer", monkeypatch)
    assert env["turn_state"] == "success" and env["version"] == 1
