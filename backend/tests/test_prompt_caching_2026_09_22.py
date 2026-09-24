"""
tests/test_prompt_caching_2026_09_22.py — Section 16 cost-efficiency
fix: prompt caching for the static system/house-rules block.

Covers:
  1. `services.llm._state.prompt_cache_context` — the ContextVar hint
     itself (default-inert, sets/resets correctly, isolated across
     concurrent asyncio tasks).
  2. `call_openrouter_model` — cache_control content-block shaping is
     gated on BOTH `LLM_PROMPT_CACHING_ENABLED=1` AND an explicit
     `cache_system=True` hint AND an `anthropic/` model slug; every
     other combination must produce the EXACT payload shape that
     existed before this fix (regression proof — these tests FAIL
     without the fix, since before the fix there was no flag/hint to
     assert against and the shape was unconditionally a plain string
     with no session_id, which the "off" assertions below still
     match, but the "on" assertions have nothing to turn on).
  3. `_call_deepseek`'s `_build_payload` — session_id passthrough,
     same on/off gating, no cache_control (DeepSeek doesn't use one).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

# ═══════════════════ 1. ContextVar hint itself ═══════════════════════

def test_default_context_is_fully_inert():
    from services.llm._state import get_prompt_cache_context
    ctx = get_prompt_cache_context()
    assert ctx == {"session_id": None, "cache_system": False}


@pytest.mark.asyncio
async def test_context_manager_sets_values_inside_block():
    from services.llm._state import get_prompt_cache_context, prompt_cache_context
    async with prompt_cache_context(session_id="sess-1", cache_system=True):
        ctx = get_prompt_cache_context()
        assert ctx == {"session_id": "sess-1", "cache_system": True}


@pytest.mark.asyncio
async def test_context_manager_resets_after_block():
    from services.llm._state import get_prompt_cache_context, prompt_cache_context
    async with prompt_cache_context(session_id="sess-1", cache_system=True):
        pass
    assert get_prompt_cache_context() == {"session_id": None, "cache_system": False}


@pytest.mark.asyncio
async def test_context_manager_resets_even_on_exception():
    from services.llm._state import get_prompt_cache_context, prompt_cache_context
    with pytest.raises(ValueError):
        async with prompt_cache_context(session_id="sess-1", cache_system=True):
            raise ValueError("boom")
    assert get_prompt_cache_context() == {"session_id": None, "cache_system": False}


@pytest.mark.asyncio
async def test_concurrent_tasks_do_not_leak_context():
    """Two 'requests' in flight at once must never see each other's hint
    — same isolation guarantee the module docstring claims."""
    from services.llm._state import get_prompt_cache_context, prompt_cache_context

    seen = {}

    async def _worker(label, session_id):
        async with prompt_cache_context(session_id=session_id, cache_system=True):
            await asyncio.sleep(0.02)  # yield, let the other task run interleaved
            seen[label] = get_prompt_cache_context()["session_id"]

    await asyncio.gather(_worker("a", "session-A"), _worker("b", "session-B"))
    assert seen == {"a": "session-A", "b": "session-B"}


# ═══════════════════ 2. call_openrouter_model ═════════════════════════

class _FakeResponse:
    def __init__(self, payload_capture: list, content: str = "hi"):
        self._content = content
        self._capture = payload_capture

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}], "usage": {}}


class _FakeHttpxClient:
    """Captures every POST payload it's asked to send, for assertion."""
    def __init__(self, payload_capture: list):
        self._capture = payload_capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def post(self, url, headers=None, json=None):
        self._capture.append(json)
        return _FakeResponse(self._capture)


@pytest.fixture(autouse=True)
def _openrouter_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")


async def _call_and_capture(model: str, monkeypatch) -> list:
    """Runs call_openrouter_model against a fake httpx client, returns
    the list of payload dicts it tried to POST (usually length 1 — the
    fake always succeeds on the first candidate)."""
    from services.llm.openrouter_client import call_openrouter_model
    captured: list = []
    with patch("httpx.AsyncClient", return_value=_FakeHttpxClient(captured)):
        await call_openrouter_model(model=model, system="You are a helpful assistant.", user="hi")
    return captured


@pytest.mark.asyncio
async def test_flag_off_content_stays_plain_string_even_if_caller_opts_in(monkeypatch):
    """Regression proof: with the flag OFF (today's default), a caller
    that sets cache_system=True must see ZERO change — plain string,
    no session_id key at all."""
    monkeypatch.setenv("LLM_PROMPT_CACHING_ENABLED", "0")
    from services.llm._state import prompt_cache_context
    async with prompt_cache_context(session_id="sess-1", cache_system=True):
        captured = await _call_and_capture("anthropic/claude-sonnet-4.5", monkeypatch)
    payload = captured[0]
    assert payload["messages"][0]["content"] == "You are a helpful assistant."
    assert "session_id" not in payload


@pytest.mark.asyncio
async def test_flag_on_no_opt_in_stays_plain_string(monkeypatch):
    """Flag ON, but caller never touched prompt_cache_context (the
    inert default) — must be indistinguishable from flag-off."""
    monkeypatch.setenv("LLM_PROMPT_CACHING_ENABLED", "1")
    captured = await _call_and_capture("anthropic/claude-sonnet-4.5", monkeypatch)
    payload = captured[0]
    assert payload["messages"][0]["content"] == "You are a helpful assistant."
    assert "session_id" not in payload


@pytest.mark.asyncio
async def test_flag_on_cache_system_true_claude_model_wraps_content_block(monkeypatch):
    monkeypatch.setenv("LLM_PROMPT_CACHING_ENABLED", "1")
    from services.llm._state import prompt_cache_context
    async with prompt_cache_context(session_id="sess-1", cache_system=True):
        captured = await _call_and_capture("anthropic/claude-sonnet-4.5", monkeypatch)
    payload = captured[0]
    content = payload["messages"][0]["content"]
    assert isinstance(content, list)
    assert content == [{
        "type": "text", "text": "You are a helpful assistant.",
        "cache_control": {"type": "ephemeral"},
    }]
    assert payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_flag_on_cache_system_true_non_claude_model_stays_plain_string(monkeypatch):
    """The cache_control marker is Anthropic-specific — a non-Claude
    model must NEVER get the content-block shape, even with the flag
    on and the caller opted in. (session_id still applies — that part
    isn't Anthropic-specific.)"""
    monkeypatch.setenv("LLM_PROMPT_CACHING_ENABLED", "1")
    from services.llm._state import prompt_cache_context
    async with prompt_cache_context(session_id="sess-1", cache_system=True):
        captured = await _call_and_capture("z-ai/glm-5.2", monkeypatch)
    payload = captured[0]
    assert payload["messages"][0]["content"] == "You are a helpful assistant."
    assert payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_flag_on_cache_system_false_stays_plain_string(monkeypatch):
    """Opted-in session_id but explicitly cache_system=False (e.g. iter
    1 of a fresh turn) — no content_control marker, session_id still
    passes through for the automatic-caching providers."""
    monkeypatch.setenv("LLM_PROMPT_CACHING_ENABLED", "1")
    from services.llm._state import prompt_cache_context
    async with prompt_cache_context(session_id="sess-1", cache_system=False):
        captured = await _call_and_capture("anthropic/claude-sonnet-4.5", monkeypatch)
    payload = captured[0]
    assert payload["messages"][0]["content"] == "You are a helpful assistant."
    assert payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_flag_on_no_session_id_key_omitted(monkeypatch):
    monkeypatch.setenv("LLM_PROMPT_CACHING_ENABLED", "1")
    from services.llm._state import prompt_cache_context
    async with prompt_cache_context(session_id=None, cache_system=True):
        captured = await _call_and_capture("anthropic/claude-sonnet-4.5", monkeypatch)
    payload = captured[0]
    assert "session_id" not in payload


# ═══════════════════ 3. _call_deepseek payload (session_id only) ═════

@pytest.mark.asyncio
async def test_deepseek_payload_flag_off_no_session_id(monkeypatch):
    monkeypatch.setenv("LLM_PROMPT_CACHING_ENABLED", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from services.llm._state import prompt_cache_context
    from services.llm.openrouter_providers import _call_deepseek

    captured: list = []

    async def _fake_post(url, headers=None, json=None):
        captured.append(json)
        return _FakeResponse(captured)

    fake_client = _FakeHttpxClient(captured)
    with patch("httpx.AsyncClient", return_value=fake_client), \
         patch("services.retry_guard.get_breaker") as mock_breaker:
        mock_breaker.return_value.allow.return_value = True
        mock_breaker.return_value.record_success.return_value = None
        async with prompt_cache_context(session_id="sess-1", cache_system=True):
            await _call_deepseek(messages=[{"role": "user", "content": "hi"}], system="static rules")
    assert "session_id" not in captured[0]


@pytest.mark.asyncio
async def test_deepseek_payload_flag_on_session_id_present(monkeypatch):
    monkeypatch.setenv("LLM_PROMPT_CACHING_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from services.llm._state import prompt_cache_context
    from services.llm.openrouter_providers import _call_deepseek

    captured: list = []
    fake_client = _FakeHttpxClient(captured)
    with patch("httpx.AsyncClient", return_value=fake_client), \
         patch("services.retry_guard.get_breaker") as mock_breaker:
        mock_breaker.return_value.allow.return_value = True
        mock_breaker.return_value.record_success.return_value = None
        async with prompt_cache_context(session_id="sess-1", cache_system=True):
            await _call_deepseek(messages=[{"role": "user", "content": "hi"}], system="static rules")
    assert captured[0]["session_id"] == "sess-1"
    # DeepSeek never gets a cache_control marker — confirm the system
    # message stays a plain string even with cache_system=True.
    assert captured[0]["messages"][0]["content"] == "static rules"
