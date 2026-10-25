"""tests/test_regression_iter2026_14b_part5_redteam.py

Master Prompt v2 PART 5.3 — red-team coverage for the wording sweep.

  - Email template injection via user name: funnel_nudge_cron.py's
    render_text() interpolates the user's own `name` field into a
    PLAIN-TEXT (not HTML) email body via _first_name(), which calls
    `.split()[0]` — Python's default split() breaks on ANY whitespace
    including \\r/\\n, so the result can never itself contain a
    newline: CRLF header injection via this field is impossible by
    construction, not by luck. Proven here with a literal CRLF
    payload in the name.
  - Sitemap/robots.txt leaking old numbers: re-confirmed clean (the
    PART 5.0 inventory found zero hits in either file).
  - Search-engine-visible old pricing: listed, not re-tested here —
    see the PART 5 report's URL list (no external site calls made).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_FRONTEND_PUBLIC = Path(__file__).resolve().parents[2] / "frontend" / "public"


class TestEmailNameInjection:
    def test_first_name_strips_crlf_payload_by_construction(self):
        from services.funnel_nudge_cron import _first_name
        malicious = {"name": "Evil\r\nBcc: attacker@evil.test\r\nSubject: pwned"}
        first = _first_name(malicious)
        assert "\r" not in first
        assert "\n" not in first
        assert first == "Evil"

    def test_first_name_falls_back_to_email_local_part_safely(self):
        from services.funnel_nudge_cron import _first_name
        assert _first_name({"email": "jane.doe@example.com"}) == "jane"

    def test_first_name_handles_empty_and_whitespace_only_fields(self):
        from services.funnel_nudge_cron import _first_name
        assert _first_name({}) == "there"
        assert _first_name({"name": "   ", "email": ""}) == "there"

    @pytest.mark.asyncio
    async def test_render_text_never_lets_the_name_inject_extra_lines_before_the_greeting(self):
        from services.funnel_nudge_cron import render_text

        class _FakeDb:
            pass

        user = {"user_id": "u1", "email": "attacker@evil.test",
                 "name": "X\r\nBcc: attacker2@evil.test"}
        text = await render_text(_FakeDb(), user, "stage3_no_chat", preview=True)
        first_line = text.splitlines()[0]
        assert first_line.startswith("Hey X,")
        assert "Bcc:" not in first_line


class TestSitemapAndRobotsStayClean:
    def test_sitemap_has_no_old_task_wording(self):
        text = (_FRONTEND_PUBLIC / "sitemap.xml").read_text(encoding="utf-8")
        assert "task" not in text.lower()
        assert "credit" not in text.lower()

    def test_robots_txt_has_no_old_task_wording(self):
        text = (_FRONTEND_PUBLIC / "robots.txt").read_text(encoding="utf-8")
        assert "task" not in text.lower()
