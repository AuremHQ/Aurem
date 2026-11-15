"""tests/test_regression_iter2026_66_web_skills_ssrf_dns_resolution.py

Devil's-advocate audit finding (Phase 2 — SSRF guard coverage audit).

services/web_skills.py's own comment claims its SSRF guard is "reused
from services/url_fetcher.py" — it is not. `_is_blocked()` was an
independent, hand-rolled hostname-string check with NO DNS resolution
at all: it only compared the URL's literal hostname against a
hardcoded set/prefix list. A hostname that isn't itself "localhost" or
a literal private-range IP sailed through untouched, even if it
resolves to one — the exact DNS-rebinding class services/url_guard.py
(built for the preview_capture.py SSRF fix, C8) exists specifically to
catch.

Reachability (why this matters): `fetch_url`, `firecrawl_scrape`, and
`firecrawl_crawl_site` are real LLM-invokable chat tools
(WEB_TOOL_SPECS / invoke_web_tool). Worse, services/orchestrator.py
auto-extracts URLs from the user's raw prompt and force-invokes
`fetch_url` on them BEFORE the LLM ever runs ("forced fetch_url
pre-execution") — a user pasting a URL into chat is enough to trigger
a fetch, no LLM judgment or explicit tool-call needed.

Fix: replace the hand-rolled `_is_blocked()` with the real,
DNS-resolving, fail-closed services.url_guard.is_safe_public_http_url
— completing what the file's own (previously false) comment already
claimed to do, reusing the existing guard rather than patching the
broken one.

Test strategy: mock the guard function directly and spy on whether
the actual outbound network call (ext_client) is ever reached — this
proves the WIRING (guard runs, and a rejection short-circuits before
any network I/O) without depending on httpx's own async DNS stack,
which doesn't go through Python's synchronous socket.getaddrinfo and
so can't be reliably intercepted that way in a unit test.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestWebSkillsUsesTheRealGuard:
    pytestmark = pytest.mark.asyncio

    async def test_fetch_url_never_reaches_the_network_when_guard_rejects(self, monkeypatch):
        from services import web_skills

        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        network_called = {"yes": False}

        class _SpyClient:
            async def __aenter__(self):
                network_called["yes"] = True
                raise AssertionError("network reached despite guard rejection")

            async def __aexit__(self, *a):
                return False

        with patch.object(web_skills, "is_safe_public_http_url", return_value=False), \
             patch.object(web_skills, "ext_client", lambda *a, **k: _SpyClient()):
            res = await web_skills.fetch_url({}, {"url": "https://rebind.example.com/"})

        assert network_called["yes"] is False, (
            "fetch_url reached the network despite the guard rejecting the URL"
        )
        assert res["ok"] is False
        assert res["error"] == "No safe public URLs given"

    async def test_fetch_url_proceeds_when_guard_allows(self, monkeypatch):
        from services import web_skills

        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        network_called = {"yes": False}

        class _SpyClient:
            async def __aenter__(self):
                network_called["yes"] = True
                raise RuntimeError("stop here — we only care that we reached this point")

            async def __aexit__(self, *a):
                return False

        # __aenter__ raising propagates straight out (an exception there
        # never reaches __aexit__) — expected, we only care that the
        # network layer was reached at all.
        with (
            patch.object(web_skills, "is_safe_public_http_url", return_value=True),
            patch.object(web_skills, "ext_client", lambda *a, **k: _SpyClient()),
            pytest.raises(RuntimeError, match="stop here"),
        ):
            await web_skills.fetch_url({}, {"url": "https://example.com/"})

        assert network_called["yes"] is True, (
            "fetch_url did not even attempt the network call for a guard-approved URL"
        )

    async def test_firecrawl_scrape_rejects_before_any_network_call(self, monkeypatch):
        from services import web_skills

        monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
        with patch.object(web_skills, "is_safe_public_http_url", return_value=False), \
             patch("httpx.AsyncClient") as mock_client:
            res = await web_skills.firecrawl_scrape({}, {"url": "https://rebind.example.com/"})
        mock_client.assert_not_called()
        assert res["ok"] is False
        assert "SSRF" in res.get("error", "")

    async def test_firecrawl_crawl_site_rejects_before_any_network_call(self, monkeypatch):
        from services import web_skills

        monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
        with patch.object(web_skills, "is_safe_public_http_url", return_value=False), \
             patch("httpx.AsyncClient") as mock_client:
            res = await web_skills.firecrawl_crawl_site({}, {"url": "https://rebind.example.com/"})
        mock_client.assert_not_called()
        assert res["ok"] is False
        assert "SSRF" in res.get("error", "")


class TestNoCompetingGuardImplementationLeftBehind:
    def test_hand_rolled_is_blocked_function_is_gone(self):
        """The whole point of this fix: no second, weaker SSRF
        implementation should remain in this module to drift out of
        sync with the real guard again later."""
        from services import web_skills
        assert not hasattr(web_skills, "_is_blocked"), (
            "web_skills._is_blocked still exists — the hand-rolled, "
            "DNS-blind SSRF check should be fully removed, not left "
            "behind as dead/competing code"
        )

    def test_web_skills_imports_the_real_guard(self):
        from services import web_skills
        assert hasattr(web_skills, "is_safe_public_http_url"), (
            "web_skills.py must import services.url_guard."
            "is_safe_public_http_url directly (reuse, not reinvent)"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
