"""
tests/test_mock_short_circuit_chat_send_2026_09_08.py

MOCK_LLM=true only short-circuited chat_stream (W2 Step 2) and the v2
engine; the sync main-chat path routers/chat/turn.py::chat_send still
made real provider calls. Pins the new guard at the TOP of chat_send.
"""
import inspect

import pytest


def _arm_llm_spies(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("real LLM path reached — MOCK_LLM leak in chat_send")

    monkeypatch.setattr("services.orchestrator.call_llm_with_meta", _boom)
    monkeypatch.setattr("services.llm.call_llm", _boom)
    monkeypatch.setattr("services.orchestrator.chat_with_tools", _boom)
    monkeypatch.setattr("routers.chat.turn.chat_with_tools", _boom)
    monkeypatch.setattr("routers.chat_pre_llm.resolve_pre_llm", _boom)
    monkeypatch.setattr("routers.chat.turn.call_emergent_watchdog", _boom)
    monkeypatch.setattr("services.usage.assert_has_budget", _boom)


@pytest.mark.asyncio
async def t_mock_short_circuit_chat_send(monkeypatch):
    """With MOCK_LLM on, /chat/send returns the canned reply, provider
    'mock', and NEVER touches budget/pre-LLM/orchestrator/provider."""
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", True)
    _arm_llm_spies(monkeypatch)

    from routers.chat import turn as turn_mod

    async def _fake_current_dev(auth):
        return {"user_id": "diag-mock-send", "email": "diag@example.com", "tier": "free"}

    monkeypatch.setattr(turn_mod, "current_dev", _fake_current_dev)

    body = turn_mod.ChatBody(prompt="fix the readme", session_id="diag-mock-send-sess")
    out = await turn_mod.chat_send(body=body, authorization="Bearer x")

    assert out["provider"] == "mock"
    assert out["ok"] is True
    assert "mock mode" in out["content"].lower()
    assert "aurem-handoff" not in out["content"]
    assert out["session_id"] == "diag-mock-send-sess"
    assert out["user_id"] == "diag-mock-send"
    assert out["meta"] == {"mock": True}


test_mock_short_circuit_chat_send = t_mock_short_circuit_chat_send


def test_mock_guard_sits_before_budget_and_llm():
    """Structural: the guard precedes assert_has_budget / resolve_pre_llm
    / chat_with_tools in chat_send, and is an `if` (falls through when
    MOCK_LLM is off, leaving the real path unchanged)."""
    from routers.chat import turn as turn_mod
    src = inspect.getsource(turn_mod.chat_send)
    guard_idx = src.index("_mock_llm_on()")
    assert guard_idx < src.index("assert_has_budget")
    assert guard_idx < src.index("resolve_pre_llm")
    assert guard_idx < src.index("chat_with_tools(")


def test_mock_off_send_falls_through(monkeypatch):
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", False)
    assert llm_client.is_mock() is False
    from routers.chat.turn import MOCK_SEND_TEXT
    assert "```aurem-handoff" not in MOCK_SEND_TEXT
