"""
tests/test_part89_engine.py — Part IX audit fix (F):
ora_chat_v2/engine.py (b) propose_action fallthrough, (c1) empty-reply
retry reasoning, (c2) close-out grounding skip, (c3) str(tool_result)
truncation, (c4) multimodal history miscount, (c5) plaintext args
logged. (F(a) cache-busting reorder was judged P1/moot — NOT built.)

Offline unit tests — the real engine is loaded with its LLM, tools,
catalog, grounding check and limits stubbed; the fake LLM follows a
per-test script. Run from backend/ with the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part89_engine.py -q
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SERVICES_DIR = _BACKEND_DIR / "services"


class FakeDB:
    class _Usage:
        async def count_documents(self, *args, **kwargs):
            return 0

        def find(self, *args, **kwargs):
            async def _gen():
                if False:
                    yield None
            return _gen()

        async def insert_one(self, doc):
            return None

    def __init__(self):
        self.ora_chat_usage = self._Usage()


class FakeLLM:
    """Scripted stream_chat. Each script entry is a list of events
    for ONE call; kwargs of every call are recorded."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def stream_chat(self, *, messages, tools, reasoning, db, user_id):
        self.calls.append({"messages": [dict(m) for m in messages],
                           "reasoning": reasoning,
                           "tools": tools})
        for evt in self.script.pop(0):
            yield evt


@pytest.fixture()
def eng(monkeypatch):
    """Load the REAL engine with stubbed collaborators. Yields a
    namespace dict: engine module, fakes, and a run() helper."""
    if "services" not in sys.modules:
        pkg = types.ModuleType("services")
        pkg.__path__ = [str(_SERVICES_DIR)]
        sys.modules["services"] = pkg
    for pkg_name, path in (
            ("services.ora_chat_v2", _SERVICES_DIR / "ora_chat_v2"),
            ("services.ora_chat", _SERVICES_DIR / "ora_chat")):
        if pkg_name not in sys.modules:
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = [str(path)]
            sys.modules[pkg_name] = pkg

    llm = types.SimpleNamespace()
    tools = types.SimpleNamespace(
        TOOL_SCHEMAS=[{"function": {"name": "get_funnel_stats"}}],
        execute_calls=[],
    )

    async def _execute_tool(db, name, args, user_id=""):
        tools.execute_calls.append({"name": name, "args": args})
        return {"ok": True, "result": "done"}

    tools.execute_tool = _execute_tool
    monkeypatch.setitem(sys.modules, "services.ora_chat_v2.llm_client",
                        types.SimpleNamespace(stream_chat=None))
    monkeypatch.setitem(sys.modules, "services.ora_chat_v2.tools", tools)
    catalog = types.SimpleNamespace(
        catalog_prompt_block=lambda: "[CATALOG]",
        ACTION_CATALOG={"read_status": {
            "name": "Read status", "risk": "read",
            "description": "read-only status check"}})
    monkeypatch.setitem(sys.modules, "services.ora_chat_v2.catalog", catalog)

    async def _log_event(db, **kwargs):
        return "prop-1"

    monkeypatch.setitem(sys.modules, "services.ora_chat_v2.audit",
                        types.SimpleNamespace(log_event=_log_event))

    # Combiner fold note (2026-11-26): the stub accepts user_tz because
    # the folded engine.py passes it (backend lane D3: state_as_of in
    # the user's zone). The real build_state_block takes user_tz=None.
    async def _build_state_block(db, user_tz=None):
        return "STATE BLOCK\nas of 2026-11-25"

    monkeypatch.setitem(
        sys.modules, "services.ora_chat_v2.state_block",
        types.SimpleNamespace(build_state_block=_build_state_block))
    prompt = types.SimpleNamespace(
        MAX_TOOL_ROUNDS=4, SYSTEM_PROMPT="You are ORA.",
        _PROPOSE_ACTION_TOOL={"type": "function",
                              "function": {"name": "propose_action"}})
    monkeypatch.setitem(sys.modules, "services.ora_chat_v2.prompt", prompt)

    grounding = types.SimpleNamespace()

    async def _grounding_ok(**kwargs):
        grounding.calls.append(kwargs)
        return {"fabricated": []}

    grounding.calls = []
    grounding.run_post_response_check = _grounding_ok
    monkeypatch.setitem(sys.modules, "services.ora_chat.grounding_check",
                        grounding)

    async def _get_effective_text(admin_id):
        return ""

    monkeypatch.setitem(sys.modules, "services.ora_chat.house_rules",
                        types.SimpleNamespace(
                            get_effective_text=_get_effective_text))
    monkeypatch.setitem(sys.modules, "services.ora_chat.safety",
                        types.SimpleNamespace(
                            build_runtime_context=lambda tz: "[RUNTIME]"))
    monkeypatch.setitem(
        sys.modules, "services.ora_chat_v2.limits_store",
        types.SimpleNamespace(
            get_limits=_async_limits()))
    ledger = types.ModuleType("services.loop_token_ledger")

    @asynccontextmanager
    async def _null_ctx(*args, **kwargs):
        yield

    ledger.llm_user_context = _null_ctx
    ledger.llm_surface_context = _null_ctx
    monkeypatch.setitem(sys.modules, "services.loop_token_ledger", ledger)

    spec = importlib.util.spec_from_file_location(
        "services.ora_chat_v2.engine",
        _SERVICES_DIR / "ora_chat_v2" / "engine.py")
    assert spec is not None and spec.loader is not None
    engine = importlib.util.module_from_spec(spec)
    sys.modules["services.ora_chat_v2.engine"] = engine
    spec.loader.exec_module(engine)

    async def run(script, **kwargs):
        fake = FakeLLM(script)
        sys.modules["services.ora_chat_v2.llm_client"].stream_chat = (
            fake.stream_chat)
        events = []
        async for evt in engine.run_turn(
                FakeDB(), admin_id="admin-1",
                session={"session_id": "sess-1", "messages": []},
                user_message="do the thing", **kwargs):
            events.append(evt)
        return events, fake, tools, grounding

    return {"engine": engine, "run": run, "tools": tools,
            "grounding": grounding}


def _async_limits():
    async def _get_limits(db):
        return {"rate_limit_per_hour": 0, "daily_token_cap": 0}
    return _get_limits


def _tool_call(name, args=None, call_id="call-1"):
    return {"id": call_id, "name": name, "arguments": args or {}}


def _final(events):
    finals = [e for e in events if e["type"] == "final"]
    assert finals, f"no final event in {events}"
    return finals[0]


# ── (b) propose_action fallthrough ───────────────────────────────────

@pytest.mark.asyncio
async def test_propose_action_rejected_when_actions_disabled(eng):
    """allow_actions=False + propose_action → explicit rejection, NO
    tool_call event, NO proposal, execute_tool never called."""
    events, fake, tools, _ = await eng["run"]([
        [{"type": "tool_calls", "calls": [
            _tool_call("propose_action",
                       {"action_id": "read_status", "params": {}})]}],
        [{"type": "delta", "content": "All done."}],
    ], advise_only=True)
    assert not [e for e in events if e["type"] == "tool_call"], \
        "a rejected proposal must not yield a tool_call event"
    assert not [e for e in events if e["type"] == "action_proposal"]
    assert tools.execute_calls == []
    rejected = [e for e in events if e["type"] == "tool_result"
                and e["name"] == "propose_action"]
    assert rejected and "disabled" in rejected[0]["summary"]
    # The model gets a clear error back in its tool message.
    assert "action_not_allowed" in json.dumps(fake.calls[1]["messages"])
    assert _final(events)["content"] == "All done."


@pytest.mark.asyncio
async def test_second_propose_action_in_same_round_rejected(eng):
    """Two propose_action calls in one round → exactly one proposal;
    the second is rejected, never executed."""
    events, fake, tools, _ = await eng["run"]([
        [{"type": "tool_calls", "calls": [
            _tool_call("propose_action",
                       {"action_id": "read_status", "params": {}},
                       call_id="call-1"),
            _tool_call("propose_action",
                       {"action_id": "read_status", "params": {}},
                       call_id="call-2"),
        ]}],
        [{"type": "delta", "content": "Done."}],
    ])
    proposals = [e for e in events if e["type"] == "action_proposal"]
    assert len(proposals) == 1
    assert tools.execute_calls == []
    rejected = [e for e in events if e["type"] == "tool_result"
                and e["name"] == "propose_action"]
    assert rejected and "one action proposal" in rejected[0]["summary"]


# ── (c1) empty-reply retry reasoning ─────────────────────────────────

@pytest.mark.asyncio
async def test_empty_reply_retry_disables_reasoning(eng):
    """think_mode on + empty reply → the corrective retry must use
    reasoning=False like the other two corrective calls."""
    events, fake, tools, _ = await eng["run"]([
        [],  # main call: empty content, no tool calls
        [{"type": "delta", "content": "Recovered answer."}],
    ], think_mode=True)
    assert fake.calls[0]["reasoning"] is True  # normal round keeps it
    assert fake.calls[1]["reasoning"] is False  # retry must not re-think
    assert _final(events)["content"] == "Recovered answer."


# ── (c2) close-out grounding ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_out_runs_grounding_check(eng, monkeypatch):
    """Round-limit close-out text is grounding-checked before the
    final event; fabricated files surface in `ungrounded`."""
    eng["engine"].MAX_TOOL_ROUNDS = 2

    async def _grounding_fabricated(**kwargs):
        eng["grounding"].calls.append(kwargs)
        return {"fabricated": ["nope.py"]}

    monkeypatch.setattr(eng["grounding"], "run_post_response_check",
                        _grounding_fabricated)
    events, fake, tools, grounding = await eng["run"]([
        [{"type": "tool_calls", "calls": [_tool_call("get_funnel_stats")]}],
        [{"type": "tool_calls", "calls": [_tool_call("get_funnel_stats")]}],
        [{"type": "delta", "content": "Forced answer."}],
    ])
    assert grounding.calls, "grounding check must run on close-out"
    assert grounding.calls[-1]["reply"] == "Forced answer."
    assert _final(events)["ungrounded"] == ["nope.py"]


@pytest.mark.asyncio
async def test_close_out_grounding_failure_marks_unchecked(eng, monkeypatch):
    """If the close-out grounding check itself fails, the final
    event says so explicitly — never silently clean."""
    eng["engine"].MAX_TOOL_ROUNDS = 2

    async def _grounding_boom(**kwargs):
        raise RuntimeError("grounding down")

    monkeypatch.setattr(eng["grounding"], "run_post_response_check",
                        _grounding_boom)
    events, fake, tools, grounding = await eng["run"]([
        [{"type": "tool_calls", "calls": [_tool_call("get_funnel_stats")]}],
        [{"type": "tool_calls", "calls": [_tool_call("get_funnel_stats")]}],
        [{"type": "delta", "content": "Forced answer."}],
    ])
    assert _final(events)["ungrounded"] == ["unchecked:round_limit_close_out"]


# ── (c3) tool result JSON ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_large_tool_result_is_valid_json_with_marker(eng, monkeypatch):
    """A >12,000-char tool result reaches the model as valid JSON
    with an explicit truncation marker — not a silent Python repr."""
    async def _big_tool(db, name, args, user_id=""):
        eng["tools"].execute_calls.append({"name": name, "args": args})
        return {"data": "x" * 15000}

    monkeypatch.setattr(
        sys.modules["services.ora_chat_v2.tools"], "execute_tool", _big_tool)
    events, fake, tools, _ = await eng["run"]([
        [{"type": "tool_calls", "calls": [_tool_call("get_funnel_stats")]}],
        [{"type": "delta", "content": "Done."}],
    ])
    tool_msgs = [m for m in fake.calls[1]["messages"]
                 if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    content = tool_msgs[0]["content"]
    parsed = json.loads(content)  # must be valid JSON, not a bare cut
    assert parsed["truncated_tool_result"] is True
    assert parsed["original_chars"] > 12000
    assert parsed["preview"].startswith('{"data": "xxxx')
    assert "{'data'" not in content  # no Python repr


# ── (c4) multimodal history counting ─────────────────────────────────

def test_history_turns_counts_multimodal_list_content(eng, monkeypatch):
    """A list-content message (7,000-char text part + image) must
    estimate in the thousands of tokens, not ~1 (len(list))."""
    engine = eng["engine"]
    monkeypatch.setenv("ORA_CONTEXT_TOKEN_CEILING", "100")
    monkeypatch.setenv("ORA_TAIL_TOKEN_BUDGET", "2500")
    session = {"messages": [
        {"role": "user",
         "content": [{"type": "text", "text": "x" * 7000},
                     {"type": "image_url",
                      "image_url": {"url": "https://example.com/i.png"}}]},
        {"role": "assistant", "content": "short"},
    ], "rolling_summary": ""}
    out = engine._history_turns(session)
    # Tail budget 2500: the short reply fits, the ~3000-token
    # multimodal turn does not — the old code counted it as ~1 token
    # and would have kept it.
    assert [m.get("content") for m in out] == ["short"]


def test_session_estimate_tokens_counts_list_content(monkeypatch):
    """Same flaw in the primary path (session._estimate_tokens)."""
    if "cto_services" not in sys.modules:
        pkg = types.ModuleType("cto_services")
        pkg.__path__ = []
        sys.modules["cto_services"] = pkg
    monkeypatch.setitem(sys.modules, "cto_services.db",
                        types.SimpleNamespace(get_db=lambda: None))
    spec = importlib.util.spec_from_file_location(
        "services.ora_chat.session",
        _SERVICES_DIR / "ora_chat" / "session.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["services.ora_chat.session"] = mod
    spec.loader.exec_module(mod)
    content = [{"type": "text", "text": "x" * 7000},
               {"type": "image_url", "image_url": {"url": "u"}}]
    assert mod._estimate_tokens(content) > 2000
    assert mod._estimate_tokens("x" * 7000) > 1000


# ── (c5) redacted logging ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_round_log_redacts_secrets(eng, caplog):
    """Args carrying a token and a credential URL must appear in
    logs only in redacted form."""
    secret_args = {
        "question": ("token sk-abcdefghij1234567890abcdefghij1234 and "
                     "https://user:s3cretPass@internal.example.com/x")}
    events, fake, tools, _ = await eng["run"]([
        [{"type": "tool_calls",
          "calls": [_tool_call("get_funnel_stats", secret_args)]}],
        [{"type": "delta", "content": "Done."}],
    ])
    with caplog.at_level(logging.INFO,
                         logger="services.ora_chat_v2.engine"):
        # Re-run to capture the log line under caplog.
        await eng["run"]([
            [{"type": "tool_calls",
              "calls": [_tool_call("get_funnel_stats", secret_args)]}],
            [{"type": "delta", "content": "Done."}],
        ])
    assert "sk-abcdefghij" not in caplog.text
    assert "s3cretPass" not in caplog.text
    assert "[REDACTED]" in caplog.text
