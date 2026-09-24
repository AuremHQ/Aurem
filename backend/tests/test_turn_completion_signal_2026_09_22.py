"""
tests/test_turn_completion_signal_2026_09_22.py

Regression lock for #9 (revised): a turn closing silently on a bare
stated intention with no result and no incomplete signal.

Live repro (post-#1-fix): "implement Jev-based intent detection" ->
reply was exactly "Let me find the real intent-classification page with
narrower searches." then the turn closed as done, zero output, no
continue affordance.

The orchestrator enforces the Turn-Completion Contract internally, but
that turn returned via an executor path that bypassed it. The fix adds
a final safety net at `run_rendering_pipeline` — the ONE pipeline every
reply passes through. These tests FAIL pre-fix and PASS after.
"""
import pytest

from services.chat_helpers import (
    enforce_turn_completion_signal,
    run_rendering_pipeline,
)

_INTENT_ONLY = "Let me find the real intent-classification page with narrower searches."


# ── helper-level ────────────────────────────────────────────────────

def test_intent_only_reply_gets_a_visible_continue_signal():
    out = enforce_turn_completion_signal(_INTENT_ONLY)
    assert out != _INTENT_ONLY
    assert "want me to continue" in out.lower()
    assert out.startswith(_INTENT_ONLY)  # original text preserved, not replaced


def test_wrapping_is_idempotent():
    once = enforce_turn_completion_signal(_INTENT_ONLY)
    twice = enforce_turn_completion_signal(once)
    assert once == twice  # never double-wraps


def test_substantive_answer_is_untouched():
    real = (
        "The intent classifier lives in `services/ora_chat/intent_router.py` "
        "in `classify_intent()`. It matches keyword buckets and falls back to "
        "the LLM only when no rule fires. Nothing else needed here."
    )
    assert enforce_turn_completion_signal(real) == real


def test_intent_lead_but_real_delivery_is_untouched():
    # Opens with an intent lead but actually delivers a 3+ item list.
    delivered = (
        "Let me lay out the three flags:\n"
        "1. SEMANTIC_MODE_ROUTER_ENABLED — off\n"
        "2. LLM_PROMPT_CACHING_ENABLED — off\n"
        "3. input normalizer — wired on ChatBody"
    )
    assert enforce_turn_completion_signal(delivered) == delivered


# ── pipeline-level (the real choke point) ───────────────────────────

@pytest.mark.asyncio
async def test_pipeline_appends_continue_signal_on_intent_only():
    content, trace = await run_rendering_pipeline(
        user_message="implement Jev-based intent detection",
        content=_INTENT_ONLY,
        prior_fix_signal=False,
        retrieved_context="",
        skip=True,               # deterministic path — isolates the net
        tool_calls_run=1,
        session_id="s-9",
        user_id="u-9",
        plain_english_active=False,
        ora_panel=True,
    )
    assert "want me to continue" in content.lower()
    assert any(p["pass"] == "turn_completion_signal" and p["changed"] for p in trace["passes"])
