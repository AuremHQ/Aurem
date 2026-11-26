"""
tests/test_part89_web_skills.py — Part IX audit fix (D):
web_skills.py (b) int/.strip outside try, (c) Firecrawl circuit
breakers, (d) r.json() without JSONDecodeError handling, (e) crawl
poll timeout counting only sleeps.

Offline unit tests — the real module is loaded with a fake httpx
client (services.http.ext_client), a fake breaker registry
(services.retry_guard.get_breaker), and the SSRF guard stubbed to
allow test URLs (D(a) was WRONG-or-STALE: the guard already exists
and is not under test here). Run from backend/ with the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part89_web_skills.py -q
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SERVICES_DIR = _BACKEND_DIR / "services"


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, json_raises=False,
                 text=""):
        self.status_code = status_code
        self._json_data = json_data
        self._json_raises = json_raises
        self.text = text
        self.headers = {}

    def json(self):
        if self._json_raises:
            raise json.JSONDecodeError("Expecting value", "<body>", 0)
        return self._json_data


class FakeClient:
    """Routes post/get to per-test handlers: {(method, url_substr): fn}."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        for (method, substr), fn in self.handlers.items():
            if method == "POST" and substr in url:
                return await fn(url, kwargs)
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        for (method, substr), fn in self.handlers.items():
            if method == "GET" and substr in url:
                return await fn(url, kwargs)
        raise AssertionError(f"unexpected GET {url}")


class FakeBreaker:
    def __init__(self):
        self.open = False
        self.failures = []
        self.successes = 0

    def allow(self):
        return not self.open

    def retry_after_s(self):
        return 5.0

    def record_failure(self, reason=""):
        self.failures.append(reason)

    def record_success(self):
        self.successes += 1


@pytest.fixture()
def ws(monkeypatch):
    """Load the REAL web_skills.py with fake transport + breakers."""
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-firecrawl-key")
    if "services" not in sys.modules:
        pkg = types.ModuleType("services")
        pkg.__path__ = [str(_SERVICES_DIR)]
        sys.modules["services"] = pkg

    handlers: dict = {}
    clients: list[FakeClient] = []

    @asynccontextmanager
    async def _ext_client(dep, timeout=None):
        client = FakeClient(handlers)
        clients.append(client)
        yield client

    http_mod = types.ModuleType("services.http")
    http_mod.ext_client = _ext_client
    monkeypatch.setitem(sys.modules, "services.http", http_mod)

    breakers: dict[str, FakeBreaker] = {}

    def _get_breaker(dep):
        return breakers.setdefault(dep, FakeBreaker())

    guard_mod = types.ModuleType("services.retry_guard")
    guard_mod.get_breaker = _get_breaker
    monkeypatch.setitem(sys.modules, "services.retry_guard", guard_mod)

    spec = importlib.util.spec_from_file_location(
        "services.web_skills", _SERVICES_DIR / "web_skills.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["services.web_skills"] = mod
    spec.loader.exec_module(mod)
    # SSRF guard: allow the test URLs (guard itself not under test).
    monkeypatch.setattr(mod, "is_safe_public_http_url", lambda u: True)
    mod._handlers = handlers  # type: ignore[attr-defined]
    mod._breakers = breakers  # type: ignore[attr-defined]
    mod._clients = clients  # type: ignore[attr-defined]
    return mod


def _ok_search_results(*args, **kwargs):
    async def _fn(url, kwargs):
        return FakeResponse(200, {"results": [
            {"title": "T", "url": "https://example.com", "content": "c",
             "score": 1.0}]})
    return _fn


# ── (b) arg parsing ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_search_null_query_returns_clean_error(ws):
    res = await ws.web_search({}, {"query": None, "max_results": "abc"})
    assert res == {"ok": False, "error": "Missing required arg `query`"}


@pytest.mark.asyncio
async def test_web_search_malformed_max_results_uses_default(ws):
    ws._handlers[("POST", "/search")] = _ok_search_results()
    res = await ws.web_search({}, {"query": "hello", "max_results": "abc"})
    assert res["ok"] is True
    assert ws._clients[0].calls[0][2]["json"]["max_results"] == 5


@pytest.mark.asyncio
async def test_web_search_int_query_returns_clean_error(ws):
    res = await ws.web_search({}, {"query": 123})
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_scrape_and_crawl_bad_args_return_clean_errors(ws):
    assert (await ws.firecrawl_scrape({}, {"url": None}))["ok"] is False
    assert (await ws.firecrawl_scrape({}, {"url": 123}))["ok"] is False
    assert (await ws.firecrawl_crawl_site({}, {"url": ["x"]}))["ok"] is False


@pytest.mark.asyncio
async def test_crawl_malformed_limit_uses_default(ws):
    async def _kickoff(url, kwargs):
        return FakeResponse(200, {"id": "job-1"})

    async def _poll(url, kwargs):
        return FakeResponse(200, {"status": "completed", "data": []})

    ws._handlers[("POST", "/crawl")] = _kickoff
    ws._handlers[("GET", "/crawl/")] = _poll
    res = await ws.firecrawl_crawl_site(
        {}, {"url": "https://example.com", "limit": "abc"})
    assert res["ok"] is True
    assert ws._clients[0].calls[0][2]["json"]["limit"] == 10


@pytest.mark.asyncio
async def test_scrape_non_list_formats_defaults_to_markdown(ws):
    async def _scrape(url, kwargs):
        assert kwargs["json"]["formats"] == ["markdown"]
        return FakeResponse(200, {"success": True, "data": {"markdown": "hi"}})

    ws._handlers[("POST", "/scrape")] = _scrape
    res = await ws.firecrawl_scrape(
        {}, {"url": "https://example.com", "formats": 123})
    assert res["ok"] is True


# ── (c) circuit breakers ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_firecrawl_breaker_open_returns_unavailable(ws):
    ws._breakers.setdefault("firecrawl", FakeBreaker()).open = True
    res = await ws.firecrawl_scrape({}, {"url": "https://example.com"})
    assert res["ok"] is False
    assert "temporarily unavailable" in res["error"]
    assert "firecrawl circuit open" in res["error"]


@pytest.mark.asyncio
async def test_summarize_breaker_open_returns_unavailable(ws):
    ws._breakers.setdefault("tavily", FakeBreaker()).open = True
    res = await ws.web_search_and_summarize({}, {"query": "hello"})
    assert res["ok"] is False
    assert "temporarily unavailable" in res["error"]


@pytest.mark.asyncio
async def test_firecrawl_500_records_breaker_failure(ws):
    async def _scrape(url, kwargs):
        return FakeResponse(500, None, text="boom")

    ws._handlers[("POST", "/scrape")] = _scrape
    res = await ws.firecrawl_scrape({}, {"url": "https://example.com"})
    assert res["ok"] is False
    assert ws._breakers["firecrawl"].failures, "5xx must trip the breaker"


@pytest.mark.asyncio
async def test_firecrawl_success_records_breaker_success(ws):
    async def _scrape(url, kwargs):
        return FakeResponse(200, {"success": True, "data": {"markdown": "hi"}})

    ws._handlers[("POST", "/scrape")] = _scrape
    res = await ws.firecrawl_scrape({}, {"url": "https://example.com"})
    assert res["ok"] is True
    assert ws._breakers["firecrawl"].successes == 1


# ── (d) non-JSON responses ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_search_non_json_body_fails_cleanly(ws):
    async def _search(url, kwargs):
        return FakeResponse(200, None, json_raises=True,
                            text="<html>cloudflare</html>")

    ws._handlers[("POST", "/search")] = _search
    res = await ws.web_search({}, {"query": "hello"})
    assert res["ok"] is False
    assert "non-JSON" in res["error"]


@pytest.mark.asyncio
async def test_json_list_body_fails_cleanly(ws):
    async def _search(url, kwargs):
        return FakeResponse(200, ["not", "an", "object"])

    ws._handlers[("POST", "/search")] = _search
    res = await ws.web_search({}, {"query": "hello"})
    assert res["ok"] is False
    assert "non-JSON-object" in res["error"]


@pytest.mark.asyncio
async def test_fetch_url_non_json_body_fails_cleanly(ws):
    async def _extract(url, kwargs):
        return FakeResponse(200, None, json_raises=True, text="not json")

    ws._handlers[("POST", "/extract")] = _extract
    res = await ws.fetch_url({}, {"url": "https://example.com"})
    assert res["ok"] is False
    assert "non-JSON" in res["error"]


@pytest.mark.asyncio
async def test_scrape_and_crawl_non_json_bodies_fail_cleanly(ws):
    async def _scrape(url, kwargs):
        return FakeResponse(200, None, json_raises=True, text="nope")

    ws._handlers[("POST", "/scrape")] = _scrape
    res = await ws.firecrawl_scrape({}, {"url": "https://example.com"})
    assert res["ok"] is False and "non-JSON" in res["error"]

    async def _kickoff(url, kwargs):
        return FakeResponse(200, None, json_raises=True, text="nope")

    ws._handlers.clear()
    ws._handlers[("POST", "/crawl")] = _kickoff
    res = await ws.firecrawl_crawl_site({}, {"url": "https://example.com"})
    assert res["ok"] is False and "non-JSON" in res["error"]


# ── (e) crawl poll deadline covers request latency ───────────────────

@pytest.mark.asyncio
async def test_crawl_poll_deadline_covers_slow_requests(ws, monkeypatch):
    """Each poll request blocks 0.5s and always says 'running'.
    With a 2s budget the call must return in well under the old
    worst case (~360s wall for a 90s sleep-only budget) — the
    deadline counts request latency, not just sleeps."""
    monkeypatch.setattr(ws, "FIRECRAWL_CRAWL_POLL_MAX", 2.0)

    async def _kickoff(url, kwargs):
        await asyncio.sleep(0.3)
        return FakeResponse(200, {"id": "job-1"})

    async def _poll(url, kwargs):
        await asyncio.sleep(0.5)
        return FakeResponse(200, {"status": "running", "data": []})

    ws._handlers[("POST", "/crawl")] = _kickoff
    ws._handlers[("GET", "/crawl/")] = _poll
    t0 = time.monotonic()
    res = await ws.firecrawl_crawl_site({}, {"url": "https://example.com"})
    elapsed = time.monotonic() - t0
    assert res["ok"] is False
    assert "still running" in res["error"]
    assert elapsed < 5.0, f"poll loop overran its budget: {elapsed:.1f}s"
    # The error reports the ACTUAL elapsed wall time, not the nominal cap.
    assert "budget 2s" in res["error"]
