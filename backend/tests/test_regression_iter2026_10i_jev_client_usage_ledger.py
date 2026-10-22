"""tests/test_regression_iter2026_10i_jev_client_usage_ledger.py

Master Prompt Step B5 — services/llm/jev_client.py already computed a
real `usage` dict (confirmed by the Step A3 audit) but never forwarded
it to services/loop_token_ledger.py::log_real_usage, making every Jev
decision call invisible to the token-billing ledger (real_llm_usage).
Fixed by forwarding `usage` right where it's already built in
ask_decisions(). Best-effort — a ledger failure must never break an
otherwise-successful Jev call.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm import jev_client


def _fake_resp(model="jev-1.13.0", answers=None, usage=None):
    r = MagicMock()
    r.model = model
    r.answers = answers or {}
    r.usage = usage or {}
    return r


def _fake_choice_answer(choice, probabilities):
    a = MagicMock(spec=["choice", "probabilities"])
    a.choice = choice
    a.probabilities = probabilities
    return a


@pytest.mark.asyncio
async def test_ask_decisions_forwards_real_usage_to_the_billing_ledger(monkeypatch):
    monkeypatch.setenv("EMERGENT_LLM_KEY", "sk-emergent-test")
    jev_client._client.cache_clear()
    fake_client = MagicMock()
    fake_client.system_one = AsyncMock(return_value=_fake_resp(
        answers={"answer": _fake_choice_answer("CODE_CHANGE", {"CODE_CHANGE": 0.99})},
        usage={"input_tokens": 441, "output_tokens": 45, "cost": 0.0000186},
    ))
    monkeypatch.setattr(jev_client, "_client", lambda: fake_client)

    with patch("services.loop_token_ledger.log_real_usage", AsyncMock()) as log_mock:
        result = await jev_client.ask_decisions(
            state="do it",
            questions={"answer": {"type": "choice", "instructions": "x",
                                   "criteria": {"CODE_CHANGE": "...", "CASUAL_CHAT": "..."}}},
        )

    assert log_mock.await_count == 1
    _, kwargs = log_mock.call_args
    args = log_mock.call_args.args
    assert args[0] == "jev-1.13.0"
    assert args[1] == {"input_tokens": 441, "output_tokens": 45, "cost": 0.0000186}
    assert kwargs.get("feature") == "jev_decisions"
    assert kwargs.get("provider") == "typesafe"
    assert result["usage"] == {"input_tokens": 441, "output_tokens": 45, "cost": 0.0000186}


@pytest.mark.asyncio
async def test_ledger_failure_never_breaks_an_otherwise_successful_call(monkeypatch):
    """The ledger write is best-effort — a crash in log_real_usage must
    not propagate and must not change the returned result."""
    monkeypatch.setenv("EMERGENT_LLM_KEY", "sk-emergent-test")
    jev_client._client.cache_clear()
    fake_client = MagicMock()
    fake_client.system_one = AsyncMock(return_value=_fake_resp(
        answers={"answer": _fake_choice_answer("CASUAL_CHAT", {"CASUAL_CHAT": 1.0})},
        usage={"input_tokens": 10, "output_tokens": 2},
    ))
    monkeypatch.setattr(jev_client, "_client", lambda: fake_client)

    with patch("services.loop_token_ledger.log_real_usage",
               AsyncMock(side_effect=RuntimeError("ledger db down"))):
        result = await jev_client.ask_decisions(
            state="hi",
            questions={"answer": {"type": "choice", "instructions": "x",
                                   "criteria": {"CODE_CHANGE": "...", "CASUAL_CHAT": "..."}}},
        )
    assert result["answers"]["answer"]["choice"] == "CASUAL_CHAT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
