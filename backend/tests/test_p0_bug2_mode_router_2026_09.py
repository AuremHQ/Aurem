"""
tests/test_p0_bug2_mode_router_2026_09.py

P0 bug-brief, Bug 2 — "Mode router hijacked by keyword presence, not
message intent." Root cause LIVE-REPRODUCED before this fix (not
assumed): worker.py's `_mode_broadcast()` used `chat_helpers.
classify_intent()` — pure regex/substring — as the SOLE dispatch
decision, with `services.mode_classifier`'s confidence score existing
but never actually gating anything. Confirmed directly via:

    classify_intent(
        "That last message failed to deliver (Cloudflare origin "
        "error). Retrying: please audit the repo for X and "
        "recommend Y.",
        f12_payload={"console_errors": [{"message": "Failed to "
                     "fetch: net::ERR_CONNECTION_RESET at chat/stream"}]},
        project_id="p_test",
    ) == "D"   # hijacked by the co-occurring real captured error,
               # even though the message's actual ask is audit+recommend

Separately confirmed `services/mode_e_auditor.py::run_audit()` accepted
`user_message` but only used it for logging — the LLM prompt never
saw it, so every audit ran the SAME generic security/quality/
performance checklist regardless of what was specifically asked.

These tests mock the network layer (Jev + the chat LLM) — no real API
calls in CI/regression runs, matching
tests/test_jev_intent_classifier_2026_09_19.py's convention.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services import chat_helpers
from services import semantic_mode_router as R
from services.llm import jev_client


BRIEF_MSG_1 = (
    "That last message failed to deliver (Cloudflare origin error). "
    "Retrying: please audit the repo for X and recommend Y."
)
BRIEF_MSG_2 = (
    "audit this repo for existing classification/routing logic and "
    "recommend whether to add X"
)
# A real captured console error — the plausible real-world signal
# attached when a message genuinely "failed to deliver".
REAL_F12_CONSOLE_ERROR = {
    "console_errors": [{"message": "Failed to fetch: net::ERR_CONNECTION_RESET at chat/stream"}]
}


def _fake_choice_answer(choice, probabilities):
    a = MagicMock(spec=["choice", "probabilities"])
    a.choice = choice
    a.probabilities = probabilities
    return a


def _jev_resp(label: str, confidence: float, other_labels=("A", "B", "C", "D", "E", "F")):
    probs = {lbl: (confidence if lbl == label else round((1 - confidence) / 5, 4)) for lbl in other_labels}
    r = MagicMock()
    r.model = "jev-1.13.0"
    r.answers = {"answer": _fake_choice_answer(label, probs)}
    r.usage = {"input_tokens": 400, "output_tokens": 40}
    return r


def _mock_jev(monkeypatch, *responses):
    """Mock `jev_client._client()` to return `responses` (one per
    call, in order) from `system_one()`. Each entry is either a fake
    response (see `_jev_resp`) or an Exception instance to raise."""
    monkeypatch.setenv("EMERGENT_LLM_KEY", "sk-emergent-test")
    jev_client._client.cache_clear()
    fake_client = MagicMock()
    fake_client.system_one = AsyncMock(side_effect=list(responses))
    monkeypatch.setattr(jev_client, "_client", lambda: fake_client)
    return fake_client


# ─── 0. Root-cause confirmation — locks in why this fix exists ─────
def test_ROOT_CAUSE_keyword_classifier_alone_misroutes_brief_message():
    """This is the bug BEFORE the fix — the raw keyword classifier
    (still the default OFF-flag path) genuinely does route the brief's
    message to D when a real console error co-occurs. If this test
    ever fails, the underlying regex vocabulary changed — re-verify
    Bug 2 is still needed / still fixed via the semantic router."""
    assert chat_helpers.classify_intent(BRIEF_MSG_1, REAL_F12_CONSOLE_ERROR, "p_test") == "D"


# ─── 1. Flag OFF (default) — byte-identical to today, ZERO network ─
@pytest.mark.asyncio
async def test_flag_off_is_byte_identical_to_keyword_classifier_and_makes_no_network_call():
    # `_client` is intentionally left unmocked — the flag-off path must
    # short-circuit before ever reaching Jev, proving the semantic path
    # is never touched when the flag is off.
    result = await R.classify_mode_semantic(BRIEF_MSG_1, REAL_F12_CONSOLE_ERROR, "p_test", "sess_1")
    assert result["source"] == "keyword"
    assert result["mode"] == chat_helpers.classify_intent(BRIEF_MSG_1, REAL_F12_CONSOLE_ERROR, "p_test")


# ─── 2. Brief's exact test: error keyword does not force Mode D ────
@pytest.mark.asyncio
async def test_error_keyword_does_not_force_debug_mode(monkeypatch):
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100")
    R._SESSION_STATE.clear()
    _mock_jev(monkeypatch, _jev_resp("E", 0.91))

    result = await R.classify_mode_semantic(BRIEF_MSG_1, REAL_F12_CONSOLE_ERROR, "p_test", "sess_brief_1")

    assert result["mode"] != "D"
    assert result["mode"] == "E"
    assert result["source"] == "jev"


# ─── 3. Confidence threshold — low-confidence signal never switches ─
@pytest.mark.asyncio
async def test_low_confidence_signal_stays_on_current_mode(monkeypatch):
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100")
    R._SESSION_STATE.clear()
    _mock_jev(monkeypatch, _jev_resp("A", 0.95), _jev_resp("D", 0.55))

    # Turn 1 — establish mode A with high confidence.
    r1 = await R.classify_mode_semantic("hey what's up", None, "p_test", "sess_thresh")
    assert r1["mode"] == "A"

    # Turn 2 — Jev suggests D but BELOW the 0.72 threshold. Must stay A.
    r2 = await R.classify_mode_semantic("something about an error somewhere", None, "p_test", "sess_thresh")
    assert r2["mode"] == "A"
    assert r2["switched"] is False


# ─── 4. Hysteresis — a single high-confidence signal isn't enough ──
@pytest.mark.asyncio
async def test_hysteresis_requires_two_consecutive_high_confidence_signals(monkeypatch):
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100")
    R._SESSION_STATE.clear()
    _mock_jev(monkeypatch, _jev_resp("C", 0.9), _jev_resp("D", 0.9), _jev_resp("D", 0.9))

    r1 = await R.classify_mode_semantic("ship this feature", None, "p_test", "sess_hyst")
    assert r1["mode"] == "C"

    # First high-confidence D signal — must NOT switch yet.
    r2 = await R.classify_mode_semantic("wait, something broke, debug it", None, "p_test", "sess_hyst")
    assert r2["mode"] == "C"
    assert r2["switched"] is False

    # Second consecutive high-confidence D signal — NOW it switches.
    r3 = await R.classify_mode_semantic("still broken, please debug it", None, "p_test", "sess_hyst")
    assert r3["mode"] == "D"
    assert r3["switched"] is True


# ─── 5. Jev outage degrades to keyword classifier, never blocks ────
@pytest.mark.asyncio
async def test_jev_outage_falls_back_to_keyword_classifier_without_raising(monkeypatch):
    from typesafe_sdk import TypeSafeAPIError

    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100")
    R._SESSION_STATE.clear()
    _mock_jev(monkeypatch, TypeSafeAPIError(status=503, body="unavailable", headers={}))

    result = await R.classify_mode_semantic(BRIEF_MSG_2, None, "p_test", "sess_outage")

    assert result["source"] == "keyword_fallback"
    assert result["mode"] == chat_helpers.classify_intent(BRIEF_MSG_2, None, "p_test")


# ─── 6. Brief's exact test: audit request is no longer canned ──────
@pytest.mark.asyncio
async def test_audit_keyword_does_not_force_canned_healthscan(monkeypatch):
    """Locks in the mode_e_auditor.py fix: `run_audit()`'s LLM prompt
    must now actually contain the user's specific request — proving
    it's no longer silently discarded in favour of the generic
    security/quality/performance checklist."""
    from services import mode_e_auditor

    captured = {}

    async def _fake_call_llm_with_meta(*, system, user, mode, max_tokens):
        captured["system"] = system
        captured["user"] = user
        return {"content": "ISSUE: none\n---"}

    monkeypatch.setattr(mode_e_auditor, "call_llm_with_meta", _fake_call_llm_with_meta)

    await mode_e_auditor.llm_deep_audit(
        file_summaries="FILE: routers/chat.py\n...",
        repo_ctx="aurem/aurem",
        user_focus=BRIEF_MSG_2,
    )

    assert "classification/routing logic" in captured["user"]
    assert "prioritize" in captured["system"].lower() or "priorit" in captured["system"].lower()


@pytest.mark.asyncio
async def test_llm_deep_audit_with_no_user_focus_is_unchanged():
    """Backward-compat: an empty/blank user_focus (or omitted
    entirely) must produce the exact same prompt as before this fix —
    no regression for callers that don't pass it."""
    from services import mode_e_auditor

    captured = {}

    async def _fake_call_llm_with_meta(*, system, user, mode, max_tokens):
        captured["user"] = user
        return {"content": ""}

    import unittest.mock
    with unittest.mock.patch.object(mode_e_auditor, "call_llm_with_meta", _fake_call_llm_with_meta):
        await mode_e_auditor.llm_deep_audit(file_summaries="FILE: x.py\n...", repo_ctx="a/b")

    assert "The user's actual request" not in captured["user"]


# ─── 7. 2026-09-22 canary rollout — founder-approved, flag ON + slice ──
# Founder's redirect after System Audit v2: flip the flag on behind a
# small canary rather than leaving genuinely-tested code dormant. All
# 5 tests above still pass unmodified because they never set
# SEMANTIC_MODE_ROUTER_CANARY_PCT — default is 100 ("everyone", once
# the master flag is ON), so pre-canary test assumptions hold exactly.

def test_canary_default_pct_is_100_when_unset(monkeypatch):
    monkeypatch.delenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", raising=False)
    assert R._canary_pct() == 100


def test_canary_pct_0_excludes_every_session():
    import os
    os.environ["SEMANTIC_MODE_ROUTER_CANARY_PCT"] = "0"
    try:
        for sid in ("s1", "s2", "abcxyz", ""):
            assert R._in_canary(sid) is False
    finally:
        os.environ.pop("SEMANTIC_MODE_ROUTER_CANARY_PCT", None)


@pytest.mark.asyncio
async def test_canary_pct_0_falls_back_to_keyword_even_with_flag_enabled(monkeypatch):
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "0")
    R._SESSION_STATE.clear()
    # `_client` intentionally left unmocked — if this session were
    # (wrongly) sampled into the canary, the call below would blow up
    # instead of silently degrading to keyword.
    result = await R.classify_mode_semantic(BRIEF_MSG_1, REAL_F12_CONSOLE_ERROR, "p_test", "sess_no_canary")
    assert result["source"] == "keyword"
    assert result["mode"] == chat_helpers.classify_intent(BRIEF_MSG_1, REAL_F12_CONSOLE_ERROR, "p_test")


def test_canary_sampling_is_deterministic_per_session(monkeypatch):
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "10")
    for sid in ("session_abc", "session_xyz", "user_42_proj_7"):
        first = R._in_canary(sid)
        for _ in range(20):
            assert R._in_canary(sid) is first, "same session flipped buckets — sampling must be stable"


def test_canary_sampling_pct_100_includes_everyone(monkeypatch):
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100")
    for sid in ("s1", "s2", "totally_random_session_id_998877"):
        assert R._in_canary(sid) is True


def test_canary_sampling_pct_10_is_a_real_minority_not_everyone_or_nobody(monkeypatch):
    """Not a statistical/flaky assertion — just proves a 10% setting
    genuinely produces SOME in and SOME out across a spread of ids
    (guards against an off-by-one that would make it behave like 0 or
    100 regardless of the configured value)."""
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "10")
    sample = [f"session_{i}" for i in range(500)]
    in_count = sum(1 for sid in sample if R._in_canary(sid))
    assert 0 < in_count < len(sample)
    assert 30 <= in_count <= 80  # ~10% of 500 = 50, generous tolerance


@pytest.mark.asyncio
async def test_canary_pct_100_session_gets_full_semantic_routing(monkeypatch):
    """Sanity: with the master flag ON and pct=100, a brand-new
    session goes through the real semantic path exactly like the
    pre-canary Bug 2 tests above — canary=100 must not be a second,
    subtly-different code path."""
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100")
    R._SESSION_STATE.clear()
    _mock_jev(monkeypatch, _jev_resp("E", 0.91))

    result = await R.classify_mode_semantic(BRIEF_MSG_1, REAL_F12_CONSOLE_ERROR, "p_test", "sess_canary_full")
    assert result["mode"] == "E"
    assert result["source"] == "jev"
