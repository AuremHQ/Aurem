"""
tests/test_self_repair_design_guard_followup_2026_09_21.py

Follow-up to the same-day Self-Repair audit (Part 2.2). Live QA on the
deployed fix found a THIRD, previously-unaudited `self_bug.emit(
"dead_end_leak", ...)` call site — services/business_voice_filter.py's
design_refusal_guard branch — that never received the legitimate-
refusal exemption applied to the other two (no_dead_end_guard,
bail_reason). Confirmed via live ledger data: two genuinely benign
design replies ("mind if I look first?", "you'd want a specialist")
got logged as dead_end_leak bugs after the original fix shipped.

This locks in: (1) the design-guard emit site now respects
is_legitimate_refusal(), (2) the chat-facing template-correction
behavior (strip_design_refusal) is UNCHANGED — this is a bug-tracking
fix, not a behavior change to what the business owner sees.
"""
from __future__ import annotations

import pytest

from services.legitimate_refusal import is_legitimate_refusal
from services.design_refusal_guard import is_compliant_design_reply, strip_design_refusal


LIVE_FALSE_POSITIVE_MIND_IF = (
    "I'd be happy to help refresh the design! Mind if I take a quick "
    "look at your current site pages in the website first?"
)
LIVE_FALSE_POSITIVE_SPECIALIST = (
    "I focus on publishing content fixes and improvements — for brand "
    "work, you'd want a design specialist. I can help with anything "
    "in your Website though!"
)


@pytest.mark.parametrize("text", [LIVE_FALSE_POSITIVE_MIND_IF, LIVE_FALSE_POSITIVE_SPECIALIST])
def test_live_design_false_positives_are_now_recognized_as_legitimate(text):
    assert is_legitimate_refusal(text) is True


@pytest.mark.parametrize("text", [LIVE_FALSE_POSITIVE_MIND_IF, LIVE_FALSE_POSITIVE_SPECIALIST])
def test_legitimate_design_replies_still_get_template_corrected(text):
    """The bug-log fix must NOT change chat behavior — a reply that
    doesn't meet the strict P8-B "always commit to concrete
    directions now" contract still gets replaced, exemption or not."""
    assert is_compliant_design_reply(text) is False
    corrected = strip_design_refusal(text)
    assert corrected != text


@pytest.mark.asyncio
async def test_design_guard_emit_site_skips_legitimate_refusals(monkeypatch):
    """Integration-level check on the exact call site in
    business_voice_filter.py — a legitimate design refusal must not
    reach self_bug.emit() at all, even though strip_design_refusal
    still runs."""
    import services.business_voice_filter as bvf
    import services.self_bug as self_bug_mod

    emitted = []

    async def _fake_emit(event_type, evidence, context, source=None):
        emitted.append((event_type, source))

    monkeypatch.setattr(self_bug_mod, "emit", _fake_emit)

    result = await bvf.apply_business_owner_guards(
        False, LIVE_FALSE_POSITIVE_MIND_IF, "please redesign my homepage",
    )
    assert emitted == [], "a legitimate design refusal must not be logged as a self-bug"
    assert result != LIVE_FALSE_POSITIVE_MIND_IF, "the reply must still be template-corrected"


@pytest.mark.asyncio
async def test_design_guard_emit_site_still_fires_for_a_real_dead_end(monkeypatch):
    """A genuine dead-end design reply (asks for assets ORA should
    just find itself, no path forward) must still be logged."""
    import services.business_voice_filter as bvf
    import services.self_bug as self_bug_mod

    emitted = []

    async def _fake_emit(event_type, evidence, context, source=None):
        emitted.append((event_type, source))

    monkeypatch.setattr(self_bug_mod, "emit", _fake_emit)

    real_dead_end = "I can't proceed without your brand guidelines and a strategy document."
    await bvf.apply_business_owner_guards(False, real_dead_end, "please redesign my homepage")
    assert ("dead_end_leak", "design_refusal_guard") in emitted, \
        "a genuine dead-end design reply must still be flagged as a self-bug"
