"""2026-11-27 — casual language + advisor-tone fixes (Issues 5, 7).

Issue 5 — the advisor tone (ORA_PANEL_TONE, routers/chat/misc.py)
primed the model with the words "screenshot" and "/loop-stats"
(rule R6) on EVERY advisor turn, so a bare session id + "chat has
issues" made the model invent a screenshot description and demand
/loop-stats output. R6 now applies ONLY when the user actually asks
about a loop run.

Issue 7 — the same tone block (rule R7) said "ALWAYS REPLY IN
ENGLISH ... regardless of what language the user writes in", which
contradicted the main-chat mirroring rule (safety.py) and answered
Hinglish users in Devanagari Hindi. R7 now mirrors per message.
The casual path (services/intent_gateway_casual_reply.py) never got
the mirroring rule at all — its tiny system prompt had no language
instruction. It now carries the same block.

These are prompt-text guards: they assert on the exact strings the
model is instructed with, plus a behavioral check that the casual
system prompt carries the rule. (Issue 6's MessageBubble backtick
trim is frontend JS — this repo has no JS test runner wired for it;
it was verified by inspection: the trim runs only after a handoff
fence was stripped, so ordinary replies ending in code are untouched.
See the final report.)
"""
from __future__ import annotations

import pytest


class TestAdvisorToneR6:
    def test_r6_guarded_to_real_loop_questions(self):
        from routers.chat.misc import ORA_PANEL_TONE
        assert "This rule applies ONLY when the user actually asks about a" in ORA_PANEL_TONE
        assert "never describe a screenshot you were" in ORA_PANEL_TONE
        assert "never demand /loop-stats for it" in ORA_PANEL_TONE

    def test_r6_still_demands_real_state_for_loop_questions(self):
        """The guard narrows R6, it does not delete it: a real loop
        question must still be answered from /loop-stats, never guessed."""
        from routers.chat.misc import ORA_PANEL_TONE
        assert "you MUST NOT guess from the screenshot" in ORA_PANEL_TONE
        assert "`/loop-stats <loop_id>`" in ORA_PANEL_TONE


class TestAdvisorToneR7:
    def test_r7_mirrors_instead_of_forcing_english(self):
        from routers.chat.misc import ORA_PANEL_TONE
        assert "ALWAYS REPLY IN ENGLISH" not in ORA_PANEL_TONE
        assert "LANGUAGE MIRRORING" in ORA_PANEL_TONE
        assert "never Devanagari unless the" in ORA_PANEL_TONE

    def test_r7_matches_main_chat_rule(self):
        """The advisor rule must say the same thing as the main-chat
        rule in safety.py — one language policy, not two."""
        from routers.chat.misc import ORA_PANEL_TONE
        from services.ora_chat.safety import SYSTEM_PROMPT
        assert "Language mirroring" in SYSTEM_PROMPT
        assert "Language mirroring" in ORA_PANEL_TONE or "LANGUAGE MIRRORING" in ORA_PANEL_TONE


class TestCasualReplyLanguage:
    @pytest.mark.asyncio
    async def test_casual_system_prompt_carries_mirroring(self, monkeypatch):
        import services.intent_gateway_casual_reply as mod
        captured = {}

        async def fake_llm(messages, system=None, max_tokens=None, temperature=None, **kw):
            captured["system"] = system or ""
            return "Hey there!"

        import services.llm as llm_mod
        monkeypatch.setattr(llm_mod, "call_llm", fake_llm, raising=False)

        out = await mod.casual_direct_reply("hi")
        assert out == "Hey there!"
        assert "Language mirroring" in captured["system"]
        assert "English" in captured["system"] and "Hinglish" in captured["system"]
