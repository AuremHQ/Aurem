"""test_agent1_ora_reply_mismatch_repro.py — Agent 1 (reply mismatch).

Failing repro tests for "Teji gives Ora a task, Ora replies something
else". Each test exercises REAL product code (engine.run_turn,
engine._history_turns, llm_client.stream_chat) with only the DB and the
provider faked — no live model, no Mongo, no PIN.

Expected state on the pinned tree (Aurem main 5eb6fe1b): ALL tests FAIL.
Each failure is one ranked root cause in ~/workspace/tmp/ora-fix/agent1-report.md.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

import services.ora_chat_v2.engine as engine
from services.ora_chat_v2 import llm_client


# ── Fake Mongo surface (same shape as the existing v2 test files) ──
class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    async def count_documents(self, *a, **k):
        return 0

    def find(self, *a, **k):
        return _Cursor([])

    async def find_one(self, *a, **k):
        return None

    async def insert_one(self, *a, **k):
        return None

    async def update_one(self, *a, **k):
        return None


class _FakeDb:
    def __init__(self):
        self._colls = {}

    def __getattr__(self, name):
        if name not in self._colls:
            self._colls[name] = _Coll()
        return self._colls[name]

    async def command(self, *a, **k):
        return {"ok": 1}


def _run(coro_or_gen):
    async def _drain_gen():
        events = []
        async for evt in coro_or_gen:
            events.append(evt)
        return events

    if hasattr(coro_or_gen, "__aiter__"):
        coro = _drain_gen()
    else:
        coro = coro_or_gen
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _bypass_gates(monkeypatch):
    async def _ok(db, admin_id):
        return True

    monkeypatch.setattr(engine, "_rate_limit_ok", _ok)
    monkeypatch.setattr(engine, "_daily_cap_ok", _ok)

    async def _noop_log(*a, **kw):
        return None

    monkeypatch.setattr(engine, "_log_usage", _noop_log)


# ── RC-1: the current user task is sent to the model TWICE ──────────
# routers/ora_chat.py::send_message appends the user message to the
# session FIRST, then re-reads the session and passes it to run_turn;
# engine._history_turns takes the last 12 turns (which now INCLUDE the
# just-appended current message) and _run_turn_impl appends
# user_message AGAIN as the final message.
def test_rc1_current_task_appears_once_in_llm_messages(monkeypatch):
    _bypass_gates(monkeypatch)
    captured = {}

    async def _fake_stream_chat(*, messages, tools=None, reasoning=False,
                                db=None, user_id=None, max_tokens=2000):
        captured["messages"] = list(messages)
        yield {"type": "resolved", "model": "mock", "label": "mock", "source": "mock"}
        yield {"type": "delta", "content": "done"}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 2}

    monkeypatch.setattr(engine.llm_client, "stream_chat", _fake_stream_chat)
    task = "Rewrite the pricing page hero copy to mention the $9 plan"
    # Session shape EXACTLY as routers/ora_chat.py hands it over:
    # the current user message is already persisted in messages.
    session = {"session_id": "s1", "messages": [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": task},
    ]}
    _run(engine.run_turn(_FakeDb(), admin_id="u1", session=session,
                         user_message=task))
    occurrences = [m for m in captured["messages"]
                   if m.get("role") == "user" and m.get("content") == task]
    assert len(occurrences) == 1, (
        f"current task sent {len(occurrences)}x to the model "
        f"(last 3 messages: {captured['messages'][-3:]})")


# ── RC-2: no temperature / top_p is ever sent to the provider ───────
# llm_client.stream_chat builds client.chat.completions.create(...) with
# model/messages/tools/max_tokens only. v1's router (0.1-0.4 temps) is
# never consulted by the v2 engine, and llm_config "params" are dropped
# by _resolve. The provider default (1.0 for OpenAI-compatible APIs)
# applies — the hottest, most wandering setting.
def test_rc2_provider_call_sets_temperature(monkeypatch):
    captured = {}

    class _Delta:
        def __init__(self):
            self.content = "ok"
            self.tool_calls = None

    class _Choice:
        def __init__(self):
            self.delta = _Delta()
            self.finish_reason = "stop"

    class _Chunk:
        def __init__(self):
            self.choices = [_Choice()]
            self.usage = None

    class _Stream:
        def __aiter__(self):
            self._it = iter([_Chunk()])
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _Stream()

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv("LLM_BASE_URL", "http://fake.invalid")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", False)

    async def _drain():
        return [e async for e in llm_client.stream_chat(
            messages=[{"role": "user", "content": "hi"}], db=None, user_id=None)]

    _run(_drain())
    assert "temperature" in captured, (
        f"no temperature sent to provider; kwargs keys={sorted(captured)}")
    assert "top_p" in captured, (
        f"no top_p sent to provider; kwargs keys={sorted(captured)}")


# ── RC-3: max_tokens truncation is silent ───────────────────────────
# stream_chat caps at max_tokens=2000 and never inspects
# finish_reason; engine accepts any non-empty round_text as the final
# answer. A reply cut off at the token cap is presented as complete.
def test_rc3_length_truncation_is_signalled(monkeypatch):
    captured_calls = {}

    class _Delta:
        def __init__(self):
            self.content = "half an answer that stops mid-"
            self.tool_calls = None

    class _Choice:
        def __init__(self):
            self.delta = _Delta()
            self.finish_reason = "length"

    class _Chunk:
        def __init__(self):
            self.choices = [_Choice()]
            self.usage = None

    class _Stream:
        def __aiter__(self):
            self._it = iter([_Chunk()])
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _Completions:
        async def create(self, **kwargs):
            captured_calls.update(kwargs)
            return _Stream()

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv("LLM_BASE_URL", "http://fake.invalid")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", False)

    async def _drain():
        return [e async for e in llm_client.stream_chat(
            messages=[{"role": "user", "content": "write a long plan"}],
            db=None, user_id=None)]

    events = _run(_drain())
    assert captured_calls.get("max_tokens") == 2000
    assert any(e.get("type") in ("error", "truncated", "length_truncated")
               or e.get("finish_reason") == "length" for e in events), (
        f"length-truncated reply surfaced with no signal: {events}")


# ── RC-4: history beyond 12 turns is silently dropped, no summary ──
# v2 uses its own _history_turns (last 12 turns / 24 msgs). The
# rolling-summary machinery (session.build_llm_messages /
# maybe_update_summary) is NEVER called on the v2 path, so anything
# older vanishes with no trace in the prompt.
def test_rc4_old_turns_preserved_or_summarised():
    msgs = []
    for i in range(15):
        msgs.append({"role": "user", "content": f"task {i}: do X{i}"})
        msgs.append({"role": "assistant", "content": f"did X{i}"})
    kept = engine._history_turns({"messages": msgs})
    assert len(kept) == len(msgs), (
        f"history window kept {len(kept)}/{len(msgs)} msgs; "
        f"oldest kept: {kept[0]['content']!r} — tasks 0-2 dropped silently")
    import pathlib
    src = pathlib.Path(engine.__file__).read_text()
    assert "build_llm_messages" in src or "maybe_update_summary" in src, (
        "v2 engine never calls the rolling-summary builder")


# ── RC-5: anti-fabrication regen is OFF by default ──────────────────
# engine.py gates the post-response grounding regen on
# ORA_REGEN_ON_FABRICATION == "1"; default is "0". A draft citing a
# nonexistent file reaches the user unchallenged unless the founder
# knew to flip an undocumented env var.
def test_rc5_fabricated_citation_triggers_regen(monkeypatch):
    _bypass_gates(monkeypatch)
    grounding_calls = []

    async def _fake_grounding(**kwargs):
        grounding_calls.append(kwargs)
        return {"fabricated": ["backend/services/does_not_exist.py"]}

    monkeypatch.setattr(engine.ora_grounding, "run_post_response_check",
                        _fake_grounding)
    monkeypatch.delenv("ORA_REGEN_ON_FABRICATION", raising=False)

    async def _fake_stream_chat(*, messages, tools=None, reasoning=False,
                                db=None, user_id=None, max_tokens=2000):
        yield {"type": "resolved", "model": "mock", "label": "mock", "source": "mock"}
        yield {"type": "delta", "content": "See backend/services/does_not_exist.py"}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 2}

    monkeypatch.setattr(engine.llm_client, "stream_chat", _fake_stream_chat)
    events = _run(engine.run_turn(
        _FakeDb(), admin_id="u1", session={"session_id": "s1", "messages": []},
        user_message="where is the pricing logic?"))
    assert grounding_calls, (
        f"grounding check never ran; final was: "
        f"{[e for e in events if e.get('type') == 'final']}")


# ── RC-6: tool results are truncated to 4000 chars before round 2 ──
# engine.py feeds tool results back as str(tool_result)[:4000]. A long
# read_source_file / web_inspect result is cut mid-content, so the
# closing answer is grounded in a truncated fragment.
def test_rc6_full_tool_result_fed_back(monkeypatch):
    _bypass_gates(monkeypatch)
    captured = {}
    calls = {"n": 0}

    async def _fake_stream_chat(*, messages, tools=None, reasoning=False,
                                db=None, user_id=None, max_tokens=2000):
        calls["n"] += 1
        captured.setdefault("rounds", []).append(list(messages))
        yield {"type": "resolved", "model": "mock", "label": "mock", "source": "mock"}
        if calls["n"] == 1:
            yield {"type": "tool_calls", "calls": [
                {"id": "c1", "name": "get_repo_tree", "arguments": {}}]}
        else:
            yield {"type": "delta", "content": "answer based on the tree"}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 2}

    async def _fake_tool(db, name, args, user_id=""):
        return {"tree": "x" * 9000}

    monkeypatch.setattr(engine.llm_client, "stream_chat", _fake_stream_chat)
    monkeypatch.setattr(engine.tools_mod, "execute_tool", _fake_tool)
    _run(engine.run_turn(
        _FakeDb(), admin_id="u1", session={"session_id": "s1", "messages": []},
        user_message="what is in the repo?"))
    round2 = captured["rounds"][1]
    tool_msgs = [m for m in round2 if m.get("role") == "tool"]
    assert tool_msgs and len(tool_msgs[0]["content"]) >= 9000, (
        f"tool result fed back truncated to "
        f"{len(tool_msgs[0]['content']) if tool_msgs else 0} chars, "
        f"not the full 9000+")
