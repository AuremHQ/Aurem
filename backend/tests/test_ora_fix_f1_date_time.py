"""test_ora_fix_f1_date_time.py — F1 regression tests (ora-fix, 2026-11-25).

Proves the model-bound system prompt on a /message turn carries a
server-side date/time line computed fresh from the real clock, in the
founder's timezone (America/Toronto) — including the evening case
where the old Asia/Kolkata default reported TOMORROW's date.

Same fake-LLM capture harness as the Agent 2 repro file: real
engine.run_turn, only the DB and the provider faked.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import datetime, timezone

import pytest

# --- stub cto_services.db BEFORE importing engine (real one needs fastapi). ---
_cto = types.ModuleType("cto_services")
_cto.__path__ = [os.path.join(os.path.dirname(__file__), "..", "cto_services")]
_dbmod = types.ModuleType("cto_services.db")
_dbmod.get_db = lambda: None
_cto.db = _dbmod
sys.modules.setdefault("cto_services", _cto)
sys.modules.setdefault("cto_services.db", _dbmod)

from services.ora_chat_v2 import engine  # noqa: E402


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


async def _capture_run_turn(session, user_message, user_tz=None):
    """Run a real run_turn against a capturing fake LLM; return messages."""
    captured = {}

    async def _fake_stream(*, messages, tools, reasoning, db, user_id):
        captured["messages"] = messages
        yield {"type": "resolved", "model": "fake", "label": "fake"}
        yield {"type": "delta", "content": "ok"}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 2}

    _orig_stream_chat = engine.llm_client.stream_chat
    engine.llm_client.stream_chat = _fake_stream
    try:
        events = [e async for e in engine.run_turn(
            _FakeDB(), admin_id="u1", session=session,
            user_message=user_message, user_tz=user_tz)]
    finally:
        # Restore the real stream_chat: a leaked fake would break any
        # llm_client test running later in the same pytest process.
        engine.llm_client.stream_chat = _orig_stream_chat
    assert any(e.get("type") == "final" for e in events), events
    return captured["messages"]


def test_today_line_evening_case_pins_toronto_date():
    """20:06 EST on Wed Nov 25 == 01:06 UTC (and 06:36 IST) on Thu Nov 26.
    The line must say November 25, never November 26."""
    evening_utc = datetime(2026, 11, 26, 1, 6, tzinfo=timezone.utc)
    line = engine._today_line("America/Toronto", now_utc=evening_utc)
    assert "Wednesday, November 25, 2026" in line
    assert "8:06 PM EST" in line
    assert "America/Toronto" in line
    assert "November 26" not in line


def test_today_line_invalid_tz_falls_back_to_toronto():
    line = engine._today_line("Not/AZone")
    assert "America/Toronto" in line
    assert "Today is " in line


def test_today_line_defaults_to_toronto_without_env(monkeypatch):
    monkeypatch.delenv("ORA_USER_TZ", raising=False)
    line = engine._today_line(None)
    assert "America/Toronto" in line


def test_live_turn_system_prompt_contains_correct_toronto_date():
    """Full run_turn payload: the system prompt carries a "Today is"
    line with Toronto's actual current date and time. Since the
    2026-11-27 prompt-cache reorder the date line sits at the END
    (static prompt first, dynamic content last) — it must still be
    present and correct, just no longer the opening line."""
    from zoneinfo import ZoneInfo
    messages = asyncio.run(_capture_run_turn(
        {"session_id": "s1", "messages": []}, "what time is it right now?",
        user_tz="America/Toronto"))
    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "Today is " in system
    # Date line is the tail now: after the static catalog, before the
    # runtime-context block that closes the prompt.
    assert system.index("Today is ") > system.index("[/ACTION CATALOG]")
    assert system.index("Today is ") < system.index("Runtime context")
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Toronto"))
    assert today.strftime("%A, %B %d, %Y") in system
    assert "(America/Toronto)" in system
    # The client-TZ header value is honoured, not ignored.
    assert "Runtime context" in system
    assert "now_toronto" in system


def test_live_turn_without_client_tz_still_toronto(monkeypatch):
    """No X-Client-TZ header: the fallback is Toronto, never Kolkata."""
    monkeypatch.delenv("ORA_USER_TZ", raising=False)
    messages = asyncio.run(_capture_run_turn(
        {"session_id": "s1", "messages": []}, "what day is it?"))
    system = messages[0]["content"]
    assert "America/Toronto" in system
    assert "now_kolkata" not in system
