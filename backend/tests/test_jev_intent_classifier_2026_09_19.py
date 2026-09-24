"""
tests/test_jev_intent_classifier_2026_09_19.py

TypeSafe Jev integration (typesafe/jev-1.13 via OpenRouter's
Decisions API) — added as an opt-in Layer-2b alternative to
`classify_intent_llm()` in services/ora_chat/intent_router.py.

Real, live-verified numbers this shipped with (5 messages that fall
through the regex layer, same 3-way decision, both paths hitting
real APIs — see CHANGELOG.md for the full write-up):
  - google/gemini-2.5-flash (current default): avg 761.1ms,
    avg $0.00012568/call (391 in / 3.4 out tokens x OpenRouter's
    published list price)
  - typesafe/jev-1.13:                          avg 302.6ms,
    avg $0.00001863/call (API's own reported usage.cost, exact)
  - Label agreement on those 5 messages: 5/5

These tests mock the network layer — no real API calls in CI/regression
runs. The numbers above came from a one-off live benchmark, not from
these tests.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from services.llm import jev_client
from services.ora_chat import intent_router as R


DECISIONS_URL = jev_client.DECISIONS_URL


# ─── 1. jev_client — low-level Decisions API wrapper ────────────────
@pytest.mark.asyncio
@respx.mock
async def test_ask_choice_happy_path(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    respx.post(DECISIONS_URL).mock(return_value=httpx.Response(200, json={
        "model": "typesafe/jev-1.13-20260917",
        "answers": {"answer": {"choice": "CODE_CHANGE",
                                "probabilities": {"CODE_CHANGE": 0.99, "CASUAL_CHAT": 0.01}}},
        "usage": {"input_tokens": 441, "output_tokens": 45, "cost": 1.8522e-05},
    }))
    label, meta = await jev_client.ask_choice(
        state="do it",
        instructions="Classify intent",
        criteria={"CODE_CHANGE": "an edit request", "CASUAL_CHAT": "small talk"},
    )
    assert label == "CODE_CHANGE"
    assert meta["probabilities"]["CODE_CHANGE"] == 0.99
    assert meta["usage"]["cost"] == 1.8522e-05


@pytest.mark.asyncio
@respx.mock
async def test_ask_choice_rejects_label_outside_criteria(monkeypatch):
    """A label the caller didn't offer must never be trusted, even if
    the API somehow returned one — same defensive posture as
    `_sanitize_llm_label()`'s exact-match-only rule for the chat-model
    path."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    respx.post(DECISIONS_URL).mock(return_value=httpx.Response(200, json={
        "answers": {"answer": {"choice": "SOMETHING_NOT_OFFERED", "probabilities": {}}},
        "usage": {},
    }))
    label, meta = await jev_client.ask_choice(
        state="x", instructions="y", criteria={"A": "a", "B": "b"},
    )
    assert label is None
    assert "unexpected_label" in meta["error"]


@pytest.mark.asyncio
@respx.mock
async def test_ask_choice_soft_fails_on_error_status(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    respx.post(DECISIONS_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    label, meta = await jev_client.ask_choice(
        state="x", instructions="y", criteria={"A": "a"},
    )
    assert label is None
    assert "decisions_status_503" in meta["error"]


@pytest.mark.asyncio
async def test_ask_decisions_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(jev_client.JevError):
        await jev_client.ask_decisions(state="x", questions={})


# ─── 2. intent_router.classify_intent_jev — Layer 2b ────────────────
@pytest.mark.asyncio
async def test_classify_intent_jev_maps_choice_to_intent(monkeypatch):
    async def fake_ask_choice(**kwargs):
        assert kwargs["criteria"] == R._JEV_CRITERIA
        return R.INTENT_CODE_CHANGE, {"probabilities": {"CODE_CHANGE": 0.97}, "usage": {}}
    monkeypatch.setattr("services.llm.jev_client.ask_choice", fake_ask_choice)
    intent, meta = await R.classify_intent_jev("do it")
    assert intent == R.INTENT_CODE_CHANGE
    assert meta["probabilities"]["CODE_CHANGE"] == 0.97


@pytest.mark.asyncio
async def test_classify_intent_jev_falls_back_to_unknown_on_failure(monkeypatch):
    async def fake_ask_choice(**kwargs):
        return None, {"error": "decisions_status_503: unavailable"}
    monkeypatch.setattr("services.llm.jev_client.ask_choice", fake_ask_choice)
    intent, meta = await R.classify_intent_jev("do it")
    assert intent == R.INTENT_UNKNOWN
    assert "error" in meta


@pytest.mark.asyncio
async def test_classify_intent_jev_empty_text():
    intent, meta = await R.classify_intent_jev("   ")
    assert intent == R.INTENT_UNKNOWN
    assert meta == {"reason": "empty_text"}


# ─── 3. classify_intent orchestrator — classifier="jev" opt-in ──────
@pytest.mark.asyncio
async def test_classify_intent_default_still_uses_llm_path(monkeypatch):
    """The default must be completely unchanged by this addition —
    Jev is opt-in, never a silent default swap."""
    called = {"llm": False, "jev": False}

    async def fake_one_shot(**kwargs):
        called["llm"] = True
        return "CODE_CHANGE", {"input_tokens": 10, "output_tokens": 1}, None

    async def fake_ask_choice(**kwargs):
        called["jev"] = True
        return "CODE_CHANGE", {}

    monkeypatch.setattr("services.llm.jev_client.ask_choice", fake_ask_choice)
    result = await R.classify_intent("do it", one_shot_fn=fake_one_shot)
    assert result["source"] == "llm"
    assert called["llm"] is True and called["jev"] is False


@pytest.mark.asyncio
async def test_classify_intent_jev_opt_in_routes_to_jev(monkeypatch):
    called = {"llm": False, "jev": False}

    async def fake_one_shot(**kwargs):
        called["llm"] = True
        return "CODE_CHANGE", {}, None

    async def fake_ask_choice(**kwargs):
        called["jev"] = True
        return "CASUAL_CHAT", {"probabilities": {"CASUAL_CHAT": 1.0}, "usage": {}}

    monkeypatch.setattr("services.llm.jev_client.ask_choice", fake_ask_choice)
    result = await R.classify_intent(
        "do it", one_shot_fn=fake_one_shot, classifier="jev",
    )
    assert result["source"] == "jev"
    assert result["intent"] == "CASUAL_CHAT"
    assert called["jev"] is True and called["llm"] is False


@pytest.mark.asyncio
async def test_classify_intent_regex_layer_short_circuits_before_either_classifier(monkeypatch):
    """Regex layer 1 must still win first, regardless of `classifier`
    — Jev/LLM are both Layer 2, only reached on regex UNKNOWN."""
    async def boom_llm(**kwargs):
        raise AssertionError("LLM layer must not be called")
    async def boom_jev(**kwargs):
        raise AssertionError("Jev layer must not be called")
    monkeypatch.setattr("services.llm.jev_client.ask_choice", boom_jev)
    result = await R.classify_intent(
        "Commit this fix to the repo", one_shot_fn=boom_llm, classifier="jev",
    )
    assert result["source"] == "regex"
    assert result["intent"] == R.INTENT_CODE_CHANGE


# ─── 4. /ora-chat/intent-classify endpoint — classifier param ───────
def test_intent_classify_endpoint_accepts_classifier_param():
    src = open("/app/backend/routers/ora_chat.py").read()
    block = src.split("class IntentClassifyBody(BaseModel):", 1)[1].split(
        "@router.post(\"/intent-classify\")", 1)[0]
    assert 'classifier: str = Field("llm"' in block
    endpoint_block = src.split("async def intent_classify(", 1)[1]
    assert "classifier=body.classifier" in endpoint_block
