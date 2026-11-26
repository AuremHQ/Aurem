"""NOTE 2026-11-25 (ora-fix): the four tests that documented the bugs
were flipped in place — same coverage — to assert the FIXED behaviour,
and renamed 2026-11-26 so their names match what they assert (old names
in parentheses):
- test_live_turn_system_prompt_has_today_line_and_runtime_context
  (was ..._has_no_runtime_context_only_state_as_of)
- test_runtime_context_default_timezone_is_toronto_not_kolkata
  (was ..._is_kolkata_not_toronto)
- test_latest_user_message_is_sent_once (was ..._is_sent_twice)
- test_history_survives_full_tail_with_rolling_summary
  (was ..._is_capped_at_12_turns_with_no_summary)
No test was deleted, skipped, or weakened.

Agent 2 repro — Ora reports the WRONG current time/date (2026-11-25).

Root cause (proven by the tests below, all stdlib-only apart from pytest):

1. LIVE PATH HAS NO DATE AT ALL. POST /ora-chat/message -> v2 engine
   (routers/ora_chat.py::send_message) never reads the X-Client-TZ header
   the frontend sends, never calls safety.assemble_system_prompt() /
   build_runtime_context(), and _run_turn() takes no user_tz argument.
   The only date-like string in the whole model-bound system prompt is
   `state_as_of: <UTC ISO timestamp>` buried inside the [SYSTEM STATE]
   data block (state_block.py:124) — a block the v2 prompt itself tells
   the model is "DATA, not instructions" (prompt.py rule 2). The model
   therefore has no authoritative "today is ..." line and must guess
   the date from its weights / from a bare UTC timestamp.
2. THE ORPHANED CORRECT PATH DEFAULTS TO THE WRONG TIMEZONE. The only
   callers of build_runtime_context() are /slash explain
   (routers/ora_chat.py:193) and deep_research (deep_research.py:988)
   — neither is on the /message path. And its fallback is
   Asia/Kolkata (safety.py:385), not America/Toronto. Teji is in
   Toronto (EST, UTC-5): every evening after 18:30 EST the Kolkata
   date is already TOMORROW (e.g. 20:05 EST Nov 25 == 06:35 IST Nov 26).
3. DUPLICATED LATEST MESSAGE. The router appends the user message to
   Mongo and re-fetches the session (routers/ora_chat.py:304-307),
   then run_turn() appends the SAME message again after the history
   that already contains it (engine.py:149-150), so the payload ends
   `... user:<msg>, user:<msg>`.

These tests import the real modules (engine import needs only a stub
for `cto_services.db`, whose real module pulls in fastapi — not
installed on this VM). The fake LLM captures the exact `messages`
list that would be sent to the provider.
"""
import sys
import types
from datetime import datetime, timezone

import pytest

# --- stub cto_services.db BEFORE importing engine (real one needs fastapi).
# Keep `cto_services` a real package (with __path__) so conftest's autouse
# fixture can still `import cto_services.auth`; only `.db` is stubbed,
# and sys.modules takes precedence over the real submodule. ---
import os as _os
_cto = types.ModuleType("cto_services")
_cto.__path__ = [_os.path.join(_os.path.dirname(__file__), "..", "cto_services")]
_dbmod = types.ModuleType("cto_services.db")
_dbmod.get_db = lambda: None
_cto.db = _dbmod
sys.modules.setdefault("cto_services", _cto)
sys.modules.setdefault("cto_services.db", _dbmod)

from services.ora_chat.safety import build_runtime_context  # noqa: E402
from services.ora_chat_v2 import engine, llm_client  # noqa: E402
from services.ora_chat_v2.prompt import SYSTEM_PROMPT  # noqa: E402


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
        captured["n_tools"] = len(tools or [])
        yield {"type": "resolved", "model": "fake", "label": "fake"}
        yield {"type": "delta", "content": "ok"}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 2}

    _orig_stream_chat = llm_client.stream_chat
    llm_client.stream_chat = _fake_stream
    try:
        events = [e async for e in engine.run_turn(
            _FakeDB(), admin_id="u1", session=session, user_message=user_message)]
    finally:
        # Restore the real stream_chat: a leaked fake would break any
        # llm_client test running later in the same pytest process.
        llm_client.stream_chat = _orig_stream_chat
    assert any(e.get("type") == "final" for e in events), events
    return captured["messages"]


def test_v2_system_prompt_contains_no_current_date_or_time():
    """The static v2 prompt (the live path's system prompt) has no date."""
    for needle in ("Runtime context", "now_utc", "today is", "current date"):
        assert needle not in SYSTEM_PROMPT, needle


def test_live_turn_system_prompt_has_today_line_and_runtime_context():
    """FIXED 2026-11-25 (F1/F2): a live run_turn now prepends a
    server-side "Today is ..." Toronto date line + the Runtime context
    block to the model-bound system prompt, and the current message is
    sent exactly once. state_as_of is still there — as the
    data-freshness stamp, not the clock."""
    import asyncio
    from zoneinfo import ZoneInfo
    messages = asyncio.run(_capture_run_turn(
        {"session_id": "s1", "messages": []}, "what time is it right now?"))
    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "Today is " in system
    assert "Runtime context" in system
    assert "America/Toronto" in system
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Toronto"))
    assert today.strftime("%B %d, %Y") in system
    assert "state_as_of:" in system


def test_runtime_context_default_timezone_is_toronto_not_kolkata():
    """FIXED 2026-11-25 (F1): the default timezone is now
    America/Toronto (safety.py), so in a Toronto evening the context
    reports Toronto's date — not tomorrow's Kolkata date."""
    ctx = build_runtime_context()
    assert "now_toronto" in ctx
    assert "now_kolkata" not in ctx
    utc_now = datetime.now(timezone.utc)
    from zoneinfo import ZoneInfo
    toronto = utc_now.astimezone(ZoneInfo("America/Toronto"))
    kolkata = utc_now.astimezone(ZoneInfo("Asia/Kolkata"))
    assert toronto.strftime("%B %d, %Y") in ctx
    if toronto.hour >= 18:  # evening in Toronto == next day in Kolkata
        assert toronto.date() != kolkata.date(), (toronto, kolkata)
        assert kolkata.strftime("%B %d, %Y") not in ctx


def test_runtime_context_with_toronto_gives_right_date():
    """Control: when the header value IS threaded through, the date is right."""
    from zoneinfo import ZoneInfo
    ctx = build_runtime_context(user_tz="America/Toronto")
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Toronto"))
    assert today.strftime("%B %d, %Y") in ctx
    assert "now_toronto" in ctx


def test_latest_user_message_is_sent_once():
    """FIXED 2026-11-25 (F2): the router persists the message then
    re-fetches the session, but the engine now drops the trailing
    duplicate — the payload ends with the current message ONCE."""
    import asyncio
    session = {"session_id": "s1", "messages": [
        {"role": "user", "content": "what time is it right now?"},
    ]}
    messages = asyncio.run(_capture_run_turn(session, "what time is it right now?"))
    users = [m for m in messages if m.get("role") == "user"]
    assert [m["content"] for m in users] == ["what time is it right now?"]


def test_history_survives_full_tail_with_rolling_summary():
    """FIXED 2026-11-25 (F8): history is routed through the
    rolling-summary builder — a short session is preserved whole
    (no 12-turn cut), and the engine source wires in
    build_llm_messages so over-ceiling turns survive as a summary."""
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
            for i in range(40)]
    tail = engine._history_turns({"messages": msgs})
    assert len(tail) == 40
    assert tail[0]["content"] == "m0"
    import pathlib
    src = pathlib.Path(engine.__file__).read_text()
    assert "build_llm_messages" in src or "maybe_update_summary" in src
