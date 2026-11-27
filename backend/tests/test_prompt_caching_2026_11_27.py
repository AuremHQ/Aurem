"""tests/test_prompt_caching_2026_11_27.py — prompt-caching fix (2026-11-27).

Covers the three changes the 2026-09-22 file does not:
  1. The live v2 chat path orders its system prompt static-first
     (SYSTEM_PROMPT / house rules / catalog) and puts the date/time
     lines LAST, so DeepSeek/GLM automatic prefix caching can hit.
  2. `ora_chat_v2.llm_client.stream_chat` sends the turn's sticky
     `session_id` (from the ambient prompt_cache_context) in
     `extra_body` on OpenRouter calls, and keeps the provider-reported
     `cached_tokens` in the ledger row instead of dropping it.
  3. `GET /admin/llm/prompt-cache` aggregates `real_llm_usage` into
     24h/7d cache-hit stats (pure helper + the route itself).
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from routers.admin_analytics import (
    aggregate_prompt_cache_stats,
    llm_prompt_cache_stats,
)
from services.llm._state import prompt_cache_context
from services.ora_chat_v2 import engine, llm_client
from services.ora_chat_v2.prompt import SYSTEM_PROMPT

# ═══════════════════ 1. System-prompt order (live v2 path) ═══════════

class _FakeCursor:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeColl:
    async def count_documents(self, *a, **k):
        return 0

    def find(self, *a, **k):
        return _FakeCursor()

    async def insert_one(self, *a, **k):
        return None


class _FakeDB(dict):
    def __getattr__(self, name):
        return _FakeColl()

    async def command(self, *a, **k):
        return {"ok": 1}


async def _capture_run_turn(session, user_message):
    """Run a real run_turn against a capturing fake LLM; return messages."""
    captured = {}

    async def _fake_stream(*, messages, tools, reasoning, db, user_id):
        captured["messages"] = messages
        yield {"type": "resolved", "model": "fake", "label": "fake"}
        yield {"type": "delta", "content": "ok"}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 2}

    _orig_stream_chat = llm_client.stream_chat
    llm_client.stream_chat = _fake_stream
    try:
        events = [e async for e in engine.run_turn(
            _FakeDB(), admin_id="u1", session=session, user_message=user_message)]
    finally:
        llm_client.stream_chat = _orig_stream_chat
    assert any(e.get("type") == "final" for e in events), events
    return captured["messages"]


def test_live_turn_system_prompt_is_static_first_dynamic_last():
    """The static prompt must come BEFORE the date/time lines, and the
    date/time lines must come LAST — the old order put `_today_line`
    first, which busted the cacheable prefix on every turn."""
    messages = asyncio.run(_capture_run_turn(
        {"session_id": "s1", "messages": []}, "what time is it right now?"))
    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    # Still present — the reorder moves content, it never drops it.
    assert "Today is " in system
    assert "Runtime context" in system
    assert "state_as_of:" in system
    # Static first: the system prompt's opening text precedes the date.
    assert system.index(SYSTEM_PROMPT.strip()[:40]) < system.index("Today is ")
    # Dynamic last: the date line and the runtime context are the tail.
    assert system.index("Today is ") < system.index("Runtime context")
    assert system.rindex("Runtime context") > system.index("state_as_of:")


# ═══════════ 2. stream_chat: sticky session_id + cached_tokens ═══════

async def _noop_log(*a, **k):
    return None


def _setup_fake_client(monkeypatch, base_url, chunks):
    from services.llm import _http

    monkeypatch.setenv("LLM_BASE_URL", base_url)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.delenv("MOCK_LLM", raising=False)

    class _FakeStream:
        def __init__(self, chunks):
            self._chunks = chunks

        async def __aiter__(self):
            for c in self._chunks:
                yield c

    class _FakeCompletions:
        def __init__(self):
            self.create_calls = []

        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            return _FakeStream(chunks)

    class _FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    fake = _FakeOpenAI()
    monkeypatch.setattr(_http, "get_shared_openai_client", lambda **kw: fake)
    return fake


async def _collect_stream_chat():
    import services.ora_chat_v2.llm_client as _lc
    events = []
    async for ev in _lc.stream_chat(
            messages=[{"role": "user", "content": "hi"}], db=None):
        events.append(ev)
    return events


def _chunk(text, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=text, tool_calls=None),
            finish_reason=None)],
        usage=usage)


@pytest.mark.asyncio
async def test_stream_chat_sends_sticky_session_id_on_openrouter(monkeypatch):
    """Inside a prompt_cache_context, the OpenRouter call carries the
    turn's session_id in extra_body (the sticky-routing hint)."""
    import services.loop_token_ledger as _ltl
    monkeypatch.setattr(_ltl, "log_real_usage", _noop_log)
    fake = _setup_fake_client(
        monkeypatch, "https://openrouter.ai/api/v1", [_chunk("hi")])
    async with prompt_cache_context(session_id="sess-9", cache_system=False):
        events = await _collect_stream_chat()
    assert fake.chat.completions.create_calls[0]["extra_body"] == {"session_id": "sess-9"}
    assert events[-1] == {"type": "done"}


@pytest.mark.asyncio
async def test_stream_chat_no_session_id_without_context(monkeypatch):
    """No ambient context → no session_id key at all (payload shape
    unchanged for callers that never opt in)."""
    import services.loop_token_ledger as _ltl
    monkeypatch.setattr(_ltl, "log_real_usage", _noop_log)
    fake = _setup_fake_client(
        monkeypatch, "https://openrouter.ai/api/v1", [_chunk("hi")])
    events = await _collect_stream_chat()
    assert fake.chat.completions.create_calls[0]["extra_body"] is None
    assert events[-1] == {"type": "done"}


@pytest.mark.asyncio
async def test_stream_chat_keeps_provider_cached_tokens(monkeypatch):
    """A usage chunk reporting prompt_tokens_details.cached_tokens
    must surface in the yielded usage event AND in the ledger row —
    before this fix the chat path dropped the field entirely."""
    import services.loop_token_ledger as _ltl
    logged = {}

    async def _capture_log(model, usage, **kw):
        logged["usage"] = usage

    monkeypatch.setattr(_ltl, "log_real_usage", _capture_log)
    usage = SimpleNamespace(
        prompt_tokens=100, completion_tokens=10,
        prompt_tokens_details=SimpleNamespace(cached_tokens=40))
    _setup_fake_client(
        monkeypatch, "https://openrouter.ai/api/v1", [_chunk("hi", usage=usage)])
    events = await _collect_stream_chat()
    usage_events = [e for e in events if e["type"] == "usage"]
    assert usage_events == [{"type": "usage", "input_tokens": 100,
                             "output_tokens": 10, "cached_tokens": 40}]
    assert logged["usage"]["cached_tokens"] == 40


# ═══════════ 3. Cache-stats endpoint ═════════════════════════════════

def test_aggregate_prompt_cache_stats_maths():
    rows = [
        {"model": "deepseek/deepseek-chat", "prompt_tokens": 1000,
         "cached_tokens": 800, "status": "ok"},
        {"model": "deepseek/deepseek-chat", "prompt_tokens": 1000,
         "cached_tokens": 0, "status": "ok"},
        {"model": "z-ai/glm-5.2", "prompt_tokens": 500,
         "cached_tokens": 500, "status": "ok"},
        # Failed rows carry 0 tokens and must not count as calls.
        {"model": "deepseek/deepseek-chat", "prompt_tokens": 0,
         "cached_tokens": 0, "status": "failed"},
    ]
    stats = aggregate_prompt_cache_stats(rows)
    assert stats["calls"] == 3
    assert stats["prompt_tokens"] == 2500
    assert stats["cached_tokens"] == 1300
    assert stats["cache_hit_share"] == 0.52
    assert stats["calls_with_cached_tokens"] == 2
    assert stats["by_model"]["deepseek/deepseek-chat"]["calls"] == 2
    assert stats["by_model"]["z-ai/glm-5.2"]["cached_tokens"] == 500


def test_aggregate_prompt_cache_stats_empty():
    stats = aggregate_prompt_cache_stats([])
    assert stats == {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
                     "cache_hit_share": 0.0, "calls_with_cached_tokens": 0,
                     "by_model": {}}


@pytest.mark.asyncio
async def test_llm_prompt_cache_route_splits_24h_and_7d(monkeypatch):
    """The route itself: admin-gated, reads real_llm_usage, splits
    the 24h window out of the 7d window, reports the flag state."""
    import routers.admin_analytics as _aa

    now = time.time()
    rows = [
        {"ts": now - 3600, "model": "deepseek/deepseek-chat",
         "prompt_tokens": 1000, "cached_tokens": 600, "status": "ok"},
        {"ts": now - 3 * 86400, "model": "deepseek/deepseek-chat",
         "prompt_tokens": 1000, "cached_tokens": 0, "status": "ok"},
    ]

    class _FakeCursorChain:
        def __init__(self, rows):
            self._rows = rows

        def sort(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        async def to_list(self, n):
            return list(self._rows)

    class _FakeColl:
        def __init__(self, rows):
            self._rows = rows

        def find(self, *a, **k):
            return _FakeCursorChain(self._rows)

    class _FakeDB:
        def __init__(self, rows):
            self.real_llm_usage = _FakeColl(rows)

    async def _no_admin(authorization=None):
        return None

    monkeypatch.setattr(_aa, "_require_admin", _no_admin)
    monkeypatch.setattr(_aa, "require_db", lambda: _FakeDB(rows))
    monkeypatch.delenv("LLM_PROMPT_CACHING_ENABLED", raising=False)

    body = await llm_prompt_cache_stats(authorization="x")
    assert body["flag_enabled"] is True
    assert body["last_24h"]["calls"] == 1
    assert body["last_24h"]["cached_tokens"] == 600
    assert body["last_7d"]["calls"] == 2
    assert body["last_7d"]["cached_tokens"] == 600
    assert "note" in body
