"""Regression — Iter 391 (2026-02 cost-optimization).

The Maxx-mode reviewer (and the legacy watchdog grader) swapped from
Claude Sonnet to GLM-5.3 (`z-ai/glm-5.3`) to cut per-call review cost
by ~2/3 while keeping quality comparable (GLM-5.3 matches/beats
Sonnet on SWE-bench-style agentic coding benchmarks).

Guards:
  1. `_call_glm_review` exists and targets `_GLM_REVIEW_MODEL`
     (default "z-ai/glm-5.3"), a model generation DIFFERENT from the
     drafting `_GLM_MODEL` (5.2) — a real second look, not the same
     weights grading their own homework.
  2. Maxx mode's review step calls `_call_glm_review`, NOT
     `_call_claude` — Claude must not be invoked when the primary
     GLM draft is non-empty.
  3. `call_emergent_watchdog` (legacy Maxx watchdog grader) also
     targets `_GLM_REVIEW_MODEL`, not `_CLAUDE_MODEL`.
  4. Cost-slug mappers (`_meta._provider_to_model_slug` and
     `services.customer_cost_tracker.model_slug_for_provider`) price
     the new "...+glm-review" label at GLM-5.3's rate, not GLM-5.2's
     cheaper rate — so the cost ledger doesn't silently under-price
     the review pass.
"""
import pytest

from services import llm as llm_mod
from services.llm import call_llm_with_meta, _GLM_MODEL, _GLM_REVIEW_MODEL


def test_glm_review_model_is_different_generation_from_primary():
    assert _GLM_REVIEW_MODEL == "z-ai/glm-5.3"
    assert _GLM_REVIEW_MODEL != _GLM_MODEL


def test_call_glm_review_function_exists():
    assert callable(getattr(llm_mod, "_call_glm_review", None))


@pytest.mark.asyncio
async def test_maxx_mode_never_calls_claude_when_glm_draft_present(monkeypatch):
    """Claude must NOT be invoked for the review pass — that's the
    entire point of this cost-optimization."""
    async def fake_glm(*a, **k):
        return "draft from glm"

    async def fake_glm_review(system="", user="", max_tokens=3500, temperature=0.0, feature=None):
        return "improved by glm-5.3"

    async def fake_claude(*a, **k):
        raise AssertionError("Claude must NOT be called for Maxx review anymore")

    monkeypatch.setattr(llm_mod, "_call_glm", fake_glm)
    monkeypatch.setattr(llm_mod, "_call_glm_review", fake_glm_review)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)

    res = await call_llm_with_meta(system="s", user="u", review_mode="maxx")

    assert res["provider"] == "glm-5.2+glm-review"
    assert res["model"] == _GLM_REVIEW_MODEL
    assert res["content"] == "improved by glm-5.3"
    assert res["fallback_chain"] == ["glm-5.2", "glm-5.3-review"]


@pytest.mark.asyncio
async def test_watchdog_grader_targets_glm_review_model_not_claude(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    seen_models = []

    async def fake_or(model, system, user, max_tokens=1000, temperature=0.2,
                       feature=None, reasoning=None, provider_prefs=None):
        seen_models.append(model)
        return "SCORE: 8\nISSUES: none\nVERDICT: fine"

    monkeypatch.setattr(llm_mod, "call_openrouter_model", fake_or)
    out = await llm_mod.call_emergent_watchdog("some output")

    assert out["ok"] is True
    assert seen_models == [_GLM_REVIEW_MODEL]
    assert not any(m.startswith("anthropic/claude") for m in seen_models)


def test_cost_slug_mappers_price_glm_review_at_53_rate():
    from services.llm._meta import _provider_to_model_slug
    from services.customer_cost_tracker import model_slug_for_provider
    from services.ora_chat.cost_tracker import _COST_PER_M_TOKENS

    assert _provider_to_model_slug("glm-5.2+glm-review") == "z-ai/glm-5.3"
    assert model_slug_for_provider("glm-5.2+glm-review") == "z-ai/glm-5.3"
    # And the pricing table actually has a (non-default) entry for it —
    # a missing row would silently fall through to the conservative
    # default rather than GLM-5.3's real, cheaper-than-Claude rate.
    assert "z-ai/glm-5.3" in _COST_PER_M_TOKENS
    assert _COST_PER_M_TOKENS["z-ai/glm-5.3"] < _COST_PER_M_TOKENS["anthropic/claude-sonnet-4.5"]


# ── "Max" reasoning + private-repo data-collection deny ────────────
# 2026-02 — "Max" is NOT a separate model; it's GLM-5.3 called with
# OpenRouter's unified `reasoning={"effort": "high"}` param, so review
# depth doesn't quietly regress to a lower default effort tier.
# `provider_prefs={"data_collection": "deny"}` restricts GLM calls
# (draft AND review) to OpenRouter hosts that don't store/train on
# the request — required because ORA reads customers' private repos.

@pytest.mark.asyncio
async def test_glm_review_requests_high_reasoning_and_denies_data_collection(monkeypatch):
    from services.llm.openrouter_providers import _call_glm_review, _GLM_PROVIDER_PREFS
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    seen = {}

    async def fake_call_openrouter_model(model, system, user, max_tokens=1000,
                                          temperature=0.2, feature=None,
                                          reasoning=None, provider_prefs=None):
        seen["model"] = model
        seen["reasoning"] = reasoning
        seen["provider_prefs"] = provider_prefs
        return "reviewed content"

    monkeypatch.setattr(
        "services.llm.openrouter_client.call_openrouter_model",
        fake_call_openrouter_model,
    )
    out = await _call_glm_review(system="s", user="u")

    assert out == "reviewed content"
    assert seen["model"] == _GLM_REVIEW_MODEL
    assert seen["reasoning"] == {"effort": "high"}
    assert seen["provider_prefs"] == _GLM_PROVIDER_PREFS == {"data_collection": "deny"}


@pytest.mark.asyncio
async def test_glm_primary_denies_data_collection_but_no_forced_reasoning(monkeypatch):
    """The drafting GLM-5.2 call must also protect private-repo data,
    but should NOT force a specific reasoning effort — only the
    review pass ("Max") needs the explicit high-effort pin."""
    from services.llm.openrouter_providers import _call_glm, _GLM_PROVIDER_PREFS
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    seen = {}

    async def fake_call_openrouter_model(model, system, user, max_tokens=1000,
                                          temperature=0.2, feature=None,
                                          reasoning=None, provider_prefs=None):
        seen["reasoning"] = reasoning
        seen["provider_prefs"] = provider_prefs
        return "draft content"

    monkeypatch.setattr(
        "services.llm.openrouter_client.call_openrouter_model",
        fake_call_openrouter_model,
    )
    out = await _call_glm(system="s", user="u")

    assert out == "draft content"
    assert seen["reasoning"] is None
    assert seen["provider_prefs"] == _GLM_PROVIDER_PREFS == {"data_collection": "deny"}


@pytest.mark.asyncio
async def test_call_openrouter_model_omits_reasoning_and_provider_when_not_given(monkeypatch):
    """Every OTHER caller (Claude, DeepSeek, Kimi via smart_router,
    etc.) that doesn't opt in must see the exact same payload shape
    as before this change — no stray `reasoning`/`provider` keys."""
    from services.llm.openrouter_client import call_openrouter_model
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured = {}

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "hi"}}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["payload"] = json
            return _FakeResp()

    monkeypatch.setattr("services.llm.openrouter_client.httpx.AsyncClient", _FakeClient)
    await call_openrouter_model(model="some/model", system="s", user="u")

    assert "reasoning" not in captured["payload"]
    assert "provider" not in captured["payload"]
