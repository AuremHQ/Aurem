"""
tests/test_p0_bug2_routing_eval_2026_09.py

2026-09-22 P0 bug-brief item 5 — offline routing-misclassification
regression test, same pattern as
test_iter124f_project_scoping_isolation.py's real-Mongo-free-of-LLM-
cost convention. Reproduces the founder-reported hijack: a transport-
error message that's actually asking for an audit.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from evals.runner import _routing_misclassification_test
from services.llm import jev_client

DECISIONS_URL = jev_client.DECISIONS_URL


@pytest.mark.asyncio
async def test_flag_off_reports_keyword_baseline_only_and_never_hard_fails():
    """With SEMANTIC_MODE_ROUTER_ENABLED unset/0 (this repo's actual
    default outside the current canary env), only the SOFT keyword
    baseline scorer runs — no HARD gate exists yet, matching the
    known in-progress rollout, not a silent block on every deploy."""
    import os
    prev = os.environ.pop("SEMANTIC_MODE_ROUTER_ENABLED", None)
    try:
        result = await _routing_misclassification_test()
    finally:
        if prev is not None:
            os.environ["SEMANTIC_MODE_ROUTER_ENABLED"] = prev

    scorer_names = {s["scorer"] for s in result["scorers"]}
    assert scorer_names == {"routing_keyword_baseline"}
    assert result["hard_fails"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_flag_on_adds_hard_semantic_gate_and_forces_full_canary_for_this_call(monkeypatch):
    """Even if the LIVE canary pct is small (10% in .env), this eval
    must exercise the semantic router's real answer, not silently
    fall through to keyword because the eval's own session_id missed
    the sample."""
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "1")  # deliberately tiny
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    respx.post(DECISIONS_URL).mock(return_value=httpx.Response(200, json={
        "model": "typesafe/jev-1.13-20260917",
        "answers": {"answer": {"choice": "E", "probabilities": {
            "A": 0.02, "B": 0.02, "C": 0.02, "D": 0.03, "E": 0.89, "F": 0.02,
        }}},
        "usage": {"input_tokens": 400, "output_tokens": 40, "cost": 2e-05},
    }))

    result = await _routing_misclassification_test()

    scorer_names = {s["scorer"] for s in result["scorers"]}
    assert scorer_names == {"routing_keyword_baseline", "routing_semantic_gate"}
    gate = next(s for s in result["scorers"] if s["scorer"] == "routing_semantic_gate")
    assert gate["status"] == "PASS"
    assert gate["details"]["mode"] == "E"
    assert result["hard_fails"] == 0

    # The tiny pct set above for THIS call must not leak into later code.
    import os
    assert os.environ.get("SEMANTIC_MODE_ROUTER_CANARY_PCT") == "1"


@pytest.mark.asyncio
@respx.mock
async def test_flag_on_hard_fails_if_semantic_router_regresses_to_debug_mode(monkeypatch):
    """The actual deploy-blocking assertion: if the semantic router
    ever regresses and answers 'D' for this exact adversarial message,
    the gate must HARD fail, not just log it."""
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    respx.post(DECISIONS_URL).mock(return_value=httpx.Response(200, json={
        "model": "typesafe/jev-1.13-20260917",
        "answers": {"answer": {"choice": "D", "probabilities": {
            "A": 0.02, "B": 0.02, "C": 0.02, "D": 0.89, "E": 0.03, "F": 0.02,
        }}},
        "usage": {"input_tokens": 400, "output_tokens": 40, "cost": 2e-05},
    }))

    result = await _routing_misclassification_test()

    gate = next(s for s in result["scorers"] if s["scorer"] == "routing_semantic_gate")
    assert gate["status"] == "FAIL" and gate["severity"] == "hard"
    assert result["hard_fails"] == 1
