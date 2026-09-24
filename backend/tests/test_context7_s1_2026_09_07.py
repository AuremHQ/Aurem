"""
tests/test_context7_s1_2026_09_07.py — Master Plan "BUILD QUALITY",
Phase S1 (Context7). Founder-specified 5 named tests:

    t_context7_tool_registered
    t_context7_fetches_docs
    t_context7_cache_hit
    t_context7_rate_limit_graceful
    t_context7_only_on_external

Every HTTP call is mocked — no real network hit in this suite.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services import context7_skills


@pytest.fixture(autouse=True)
def _reset_context7_caches():
    context7_skills.clear_caches()
    yield
    context7_skills.clear_caches()


def _fake_response(status_code: int, json_body: dict | None = None,
                    text: str = "", headers: dict | None = None):
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: json_body or {},
        text=text,
        headers=headers or {"content-type": "application/json"},
    )


class _FakeAsyncClient:
    """Minimal async-context-manager stand-in for `ext_client`. `gets`
    is a list of responses returned in call order (search, then
    context, then any further calls). Must NOT copy `gets` — a new
    `_FakeAsyncClient` instance is created per `ext_client(...)` call
    (once per HTTP step), and they need to share/mutate the SAME
    underlying list via `pop(0)` for call-order to work across steps."""
    def __init__(self, gets: list):
        self._gets = gets

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        return self._gets.pop(0)


def _patch_ext_client(gets: list):
    return patch.object(context7_skills, "ext_client",
                         lambda *a, **k: _FakeAsyncClient(gets))


# ── t_context7_tool_registered ──────────────────────────────────────
def test_context7_tool_registered():
    """get_library_docs is callable — both directly and via the
    LOCAL_TOOLS dispatch table it's wired into."""
    assert callable(context7_skills.get_library_docs)
    assert "get_library_docs" in context7_skills.CONTEXT7_TOOLS

    from services.local_tools import LOCAL_TOOLS, TOOL_SPECS
    assert "get_library_docs" in LOCAL_TOOLS
    assert LOCAL_TOOLS["get_library_docs"] is context7_skills.get_library_docs
    names = {s["name"] for s in TOOL_SPECS}
    assert "get_library_docs" in names

    from core.tool_router import TOOL_GROUPS
    assert "get_library_docs" in TOOL_GROUPS["code"]

    from services.tools_bridge import extract_tool_calls
    calls = extract_tool_calls("get_library_docs(library='fastapi')")
    assert any(c["tool"] == "get_library_docs" for c in calls), (
        "get_library_docs must be in tools_bridge's Python-call-syntax "
        "known-tools set, or DeepSeek/GLM's fn(...) emissions get dropped"
    )


# ── t_context7_fetches_docs ──────────────────────────────────────────
def test_context7_fetches_docs():
    """A call returns docs for a known package (mocked HTTP)."""
    search_resp = _fake_response(200, {"results": [{"id": "/fastapi/fastapi"}]})
    context_resp = _fake_response(
        200, text="FastAPI middleware docs...",
        headers={"content-type": "text/plain"},
    )
    with _patch_ext_client([search_resp, context_resp]):
        result = asyncio.run(context7_skills.get_library_docs(
            {}, {"library": "fastapi", "query": "middleware"},
        ))
    assert result["ok"] is True
    assert result["library_id"] == "/fastapi/fastapi"
    assert "FastAPI middleware docs" in result["content"]
    assert result["cache_hit"] is False


# ── t_context7_cache_hit ─────────────────────────────────────────────
def test_context7_cache_hit():
    """2nd identical (library, query) call is a cache hit — no 2nd
    HTTP round-trip for either step."""
    search_resp = _fake_response(200, {"results": [{"id": "/facebook/react"}]})
    context_resp = _fake_response(
        200, text="React hooks docs...",
        headers={"content-type": "text/plain"},
    )
    with _patch_ext_client([search_resp, context_resp]):
        first = asyncio.run(context7_skills.get_library_docs(
            {}, {"library": "react", "query": "hooks"},
        ))
    assert first["ok"] is True and first["cache_hit"] is False

    # No responses queued this time — a real HTTP call would raise
    # IndexError from `_FakeAsyncClient.get()` popping an empty list.
    with _patch_ext_client([]):
        second = asyncio.run(context7_skills.get_library_docs(
            {}, {"library": "react", "query": "hooks"},
        ))
    assert second["ok"] is True
    assert second["cache_hit"] is True
    assert second["content"] == first["content"]


# ── t_context7_rate_limit_graceful ────────────────────────────────────
def test_context7_rate_limit_graceful():
    """A 429 on a cold cache never crashes — returns a graceful note,
    not an exception, not a hang."""
    rate_limited_resp = _fake_response(429)
    with _patch_ext_client([rate_limited_resp]):
        result = asyncio.run(context7_skills.get_library_docs(
            {}, {"library": "some-lib", "query": "x"},
        ))
    assert result["ok"] is False
    assert result["rate_limited"] is True
    assert "rate-limited" in result["note"]
    assert "own knowledge" in result["note"]


def test_context7_rate_limit_falls_back_to_stale_cache():
    """A 429 on a WARM cache (after TTL expiry) returns the stale
    cached docs instead of failing — the founder's exact spec:
    'On HTTP 429: return the cached result if available'."""
    search_resp = _fake_response(200, {"results": [{"id": "/vercel/next.js"}]})
    context_resp = _fake_response(
        200, text="Next.js routing docs...",
        headers={"content-type": "text/plain"},
    )
    with _patch_ext_client([search_resp, context_resp]):
        asyncio.run(context7_skills.get_library_docs(
            {}, {"library": "next.js", "query": "routing"},
        ))
    # Force both caches to look expired, then simulate a 429 on retry.
    for cache in (context7_skills._LIB_ID_CACHE, context7_skills._DOCS_CACHE):
        for k, (v, _exp) in list(cache.items()):
            cache[k] = (v, 0.0)  # already-expired monotonic timestamp
    rate_limited_resp_1 = _fake_response(429)
    rate_limited_resp_2 = _fake_response(429)
    with _patch_ext_client([rate_limited_resp_1, rate_limited_resp_2]):
        result = asyncio.run(context7_skills.get_library_docs(
            {}, {"library": "next.js", "query": "routing"},
        ))
    assert result["ok"] is True
    assert result["rate_limited"] is True
    assert "Next.js routing docs" in result["content"]


# ── t_context7_only_on_external ────────────────────────────────────────
def test_context7_only_on_external():
    """The orchestrator prompt documents calling get_library_docs only
    for external-library code, opt-in — not forced every turn."""
    import inspect
    from services import orchestrator
    src = inspect.getsource(orchestrator)
    assert "get_library_docs" in src
    assert "OPT-IN" in src
    assert "third-party" in src
    assert "stdlib-only" in src or "user's OWN project code" in src


# ── keyless vs keyed auth headers ──────────────────────────────────────
def test_context7_keyless_by_default(monkeypatch):
    monkeypatch.delenv("CONTEXT7_API_KEY", raising=False)
    assert context7_skills._headers() == {}


def test_context7_uses_key_when_present(monkeypatch):
    monkeypatch.setenv("CONTEXT7_API_KEY", "ctx7sk-test123")
    assert context7_skills._headers() == {"Authorization": "Bearer ctx7sk-test123"}


# ── cost ledger row ──────────────────────────────────────────────────
def test_context7_cost_logged():
    """Every call writes a lightweight row (not the token/cost LLM
    shape) with library, cache_hit, latency_ms, rate_limited."""
    captured = {}

    class _FakeCollection:
        async def insert_one(self, doc):
            captured.update(doc)

    class _FakeDB:
        context7_calls = _FakeCollection()

    with patch("cto_services.db.get_db", return_value=_FakeDB()):
        asyncio.run(context7_skills.log_context7_call(
            library="fastapi", library_id="/fastapi/fastapi",
            cache_hit=False, latency_ms=42, rate_limited=False,
        ))
    assert captured["library"] == "fastapi"
    assert captured["library_id"] == "/fastapi/fastapi"
    assert captured["cache_hit"] is False
    assert captured["latency_ms"] == 42
    assert captured["rate_limited"] is False
    assert captured["cost_usd"] == 0.0
    # Must NOT be forced into the token/cost LLM shape.
    assert "input_tokens" not in captured
    assert "output_tokens" not in captured


def test_context7_cost_log_never_raises_on_db_failure():
    """Logging is best-effort — a DB failure must never propagate."""
    with patch("cto_services.db.get_db", side_effect=RuntimeError("db down")):
        asyncio.run(context7_skills.log_context7_call(
            library="x", library_id=None, cache_hit=False,
            latency_ms=1, rate_limited=False,
        ))  # must not raise
