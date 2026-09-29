"""
test_ora_chat_v2_empty_final_content.py — 2026-09-27 P0-1

Root-cause regression for "ORA founder chat replies disappear" with NO
crash and NO error — the exact production repro (session
68a32924d106454e83d99fdae7257d82, message
4cbf1dee1d354b0cb69be9c78ed91c26 saved with content "").

Reproduced live against OpenRouter (model openai/gpt-oss-20b, provider
"Darkbloom"): the model can end a turn with finish_reason="stop", zero
tool_calls AND an empty `content` string — its real answer sits only
in the separate `reasoning` channel, never copied to the visible
channel. Before the fix, `engine.run_turn` accepted `round_text` as-is
and yielded `{"type": "final", "content": ""}`, which
`routers/ora_chat.py` then persisted verbatim — the exact bug.

Fix: `run_turn` now treats "no tool_calls + empty content" as an
invalid final answer, forces one corrective tools-off completion, and
if THAT is also empty, yields a real `{"type": "error"}` event instead
of ever yielding an empty `final`.
"""
from __future__ import annotations

import asyncio

import services.ora_chat_v2.engine as engine


class _FakeDB:
    """Enough of a Mongo-like surface for _rate_limit_ok/_daily_cap_ok/
    _log_usage to no-op without touching a real database."""
    def __getattr__(self, name):
        return self

    def find_one(self, *a, **kw):
        async def _inner():
            return None
        return _inner()

    def __call__(self, *a, **kw):
        return self

    async def count_documents(self, *a, **kw):
        return 0

    async def insert_one(self, *a, **kw):
        return None

    async def update_one(self, *a, **kw):
        return None


def _run(coro_fn, *args, **kwargs):
    async def _drain():
        events = []
        async for evt in coro_fn(*args, **kwargs):
            events.append(evt)
        return events
    return asyncio.get_event_loop().run_until_complete(_drain())


def _bypass_gates(monkeypatch):
    async def _ok(db, admin_id):
        return True
    monkeypatch.setattr(engine, "_rate_limit_ok", _ok)
    monkeypatch.setattr(engine, "_daily_cap_ok", _ok)
    async def _noop_log(*a, **kw):
        return None
    monkeypatch.setattr(engine, "_log_usage", _noop_log)


def test_empty_content_no_tool_calls_triggers_corrective_call_and_succeeds(monkeypatch):
    """Round 1: model returns no tool_calls, content="" (the exact bug
    condition). Corrective retry call succeeds with real text — the
    turn must end with a real, non-empty `final`, not the empty
    string."""
    _bypass_gates(monkeypatch)
    call_log = []

    async def _fake_stream_chat(*, messages, tools=None, reasoning=False,
                                 db=None, user_id=None, max_tokens=2000):
        call_log.append({"tools": tools})
        yield {"type": "resolved", "model": "openai/gpt-oss-20b", "label": "gpt-oss-20b", "source": "env"}
        if len(call_log) == 1:
            # First (and only) round: no tool_calls, EMPTY content —
            # the exact reproduced production failure mode.
            yield {"type": "usage", "input_tokens": 500, "output_tokens": 10}
        else:
            # Forced corrective call (tools=None) — model actually
            # writes the real answer this time.
            for chunk in ["Guard 8 checks ", "commit SHA drift ", "against main."]:
                yield {"type": "delta", "content": chunk}
            yield {"type": "usage", "input_tokens": 50, "output_tokens": 8}

    monkeypatch.setattr(engine.llm_client, "stream_chat", _fake_stream_chat)

    events = _run(
        engine.run_turn, _FakeDB(), admin_id="test-user",
        session={"session_id": "test-empty-final", "messages": []},
        user_message="What does Guard 8 check?", advise_only=True,
    )
    types = [e.get("type") for e in events]
    assert "error" not in types, f"should NOT error — the corrective retry had real text: {events}"
    finals = [e for e in events if e.get("type") == "final"]
    assert finals, f"expected a final event: {events}"
    assert finals[0]["content"] == "Guard 8 checks commit SHA drift against main.", finals[0]
    # Exactly 2 upstream calls: the original empty one + one corrective retry.
    assert len(call_log) == 2, call_log
    assert call_log[1]["tools"] is None, "corrective retry must be tools=None"


def test_empty_content_no_tool_calls_and_retry_also_empty_yields_error_not_final(monkeypatch):
    """Both the original call AND the corrective retry come back
    empty — must yield a real `error` event and must NEVER yield an
    empty `final` (the literal production bug: content="" persisted
    as if it were a real reply)."""
    _bypass_gates(monkeypatch)

    async def _fake_stream_chat_always_empty(*, messages, tools=None, reasoning=False,
                                                db=None, user_id=None, max_tokens=2000):
        yield {"type": "resolved", "model": "openai/gpt-oss-20b", "label": "gpt-oss-20b", "source": "env"}
        yield {"type": "usage", "input_tokens": 500, "output_tokens": 0}

    monkeypatch.setattr(engine.llm_client, "stream_chat", _fake_stream_chat_always_empty)

    events = _run(
        engine.run_turn, _FakeDB(), admin_id="test-user",
        session={"session_id": "test-empty-final-2", "messages": []},
        user_message="What does Guard 8 check?", advise_only=True,
    )
    types = [e.get("type") for e in events]
    assert "final" not in types, (
        f"must NEVER yield an empty `final` — this is the exact "
        f"production bug (empty content silently persisted): {events}"
    )
    assert "error" in types, f"expected a real error event instead: {events}"
    err = [e for e in events if e.get("type") == "error"][0]
    assert err["error"] == "empty_model_response"
