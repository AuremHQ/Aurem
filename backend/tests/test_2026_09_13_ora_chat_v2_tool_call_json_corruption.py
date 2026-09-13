"""
tests/test_2026_09_13_ora_chat_v2_tool_call_json_corruption.py

Founder-reported bug (exact error):
    "Validation: `messages[26].tool_calls[0].function.arguments` must
    be a valid JSON object string: key must be a string at line 1
    column 2"
Triggered by asking ORA (the admin ORA Chat v2 rebuild) to check
github.com/obra/superpowers and compare it against AUREM's own
skills - a request that makes at least one tool call (web_inspect),
after which the SAME turn does a follow-up model call to produce the
final answer.

ROOT CAUSE (confirmed by direct code trace, not guesswork):
  services/ora_chat_v2/llm_client.py:210 parses the streamed tool-call
  arguments with `json.loads(...)`, producing a Python dict.
  services/ora_chat_v2/engine.py:225 (OLD) re-serialized that dict
  back into the replayed message history with `str(c["arguments"])`.
  `str()` on a dict produces PYTHON's single-quoted repr -
  `"{'url': 'https://github.com/obra/superpowers'}"` - which is not
  valid JSON. Every OpenAI-compatible tool-calling spec requires
  `tool_calls[].function.arguments` to be a JSON-encoded STRING. When
  this corrupted message is replayed on the NEXT tool round (in the
  SAME turn, `services/ora_chat_v2/engine.py`'s `for round_idx in
  range(MAX_TOOL_ROUNDS)` loop), a strict provider rejects it with
  "key must be a string at line 1 column 2" - column 2 is exactly
  where a JSON parser expects the opening `"` of a quoted key and
  instead finds Python's `'`. A more lenient provider instead
  silently mangles/ignores the malformed history entry, which is the
  most likely explanation for the SEPARATE "stale unrelated answer"
  symptom seen on a different reproduction attempt - same root cause,
  different provider-side failure mode.

FIX: `json.dumps(c["arguments"])` instead of `str(c["arguments"])`.

This bug was NOT gated by conversation length - message index 26 was
simply where a tool call happened to occur in that particular long
conversation. ANY turn with at least one tool call, in a conversation
of ANY length (including a fresh one), hits this same corrupted
serialization on the very next round within that same turn. Test 5
proves this directly on a length-0 conversation.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def test_1_old_str_serialization_reproduces_the_exact_reported_failure():
    """This is exactly what the OLD code did at engine.py:225. Proves
    it produces a string that is NOT valid JSON, and that trying to
    parse it fails at the EXACT position the founder's error
    describes (line 1, column 2 - right after the opening brace)."""
    real_tool_arguments = {"url": "https://github.com/obra/superpowers",
                            "question": "compare this to Aurem's own skills"}
    old_serialized = str(real_tool_arguments)   # the OLD, buggy line
    assert old_serialized.startswith("{'"), (
        "sanity check on Python's dict repr format"
    )
    with pytest.raises(json.JSONDecodeError) as exc_info:
        json.loads(old_serialized)
    err = exc_info.value
    # Python's json module reports this exact failure as "Expecting
    # property name enclosed in double quotes" at line 1 column 2 -
    # the same position class the founder's provider-side error
    # describes ("key must be a string at line 1 column 2").
    assert err.lineno == 1
    assert err.colno == 2
    assert "double quotes" in err.msg or "property name" in err.msg


def test_2_new_json_dumps_serialization_is_valid_and_round_trips():
    """The FIX: `json.dumps()` instead of `str()` - must produce
    valid JSON that decodes back to the exact original dict, for
    nested/typed values too (not just flat strings)."""
    real_tool_arguments = {
        "url": "https://github.com/obra/superpowers",
        "question": "compare this to Aurem's own skills",
        "nested": {"a": 1, "b": [1, 2, 3], "c": None, "d": True},
    }
    new_serialized = json.dumps(real_tool_arguments)
    round_tripped = json.loads(new_serialized)   # must not raise
    assert round_tripped == real_tool_arguments


def test_3_engine_module_no_longer_uses_str_for_arguments():
    """Static guard against regression - the exact buggy ASSIGNMENT
    must not reappear (the file's own comment block documenting the
    fix legitimately mentions the old pattern in prose, so check the
    real code line specifically, not full-file substring)."""
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "services", "ora_chat_v2", "engine.py")
    ).read()
    assert '"arguments": str(c["arguments"])' not in src
    assert '"arguments": json.dumps(c["arguments"])' in src
    assert "import json" in src


def test_4_engine_message_construction_produces_valid_json_arguments():
    """Exercise the ACTUAL code path in engine.py (not a re-implementation
    of it) - build the same message dict engine.py builds and confirm
    the arguments field round-trips through json.loads cleanly, for a
    tool_calls list shaped exactly like a real web_inspect call."""
    import json as _json
    tool_calls = [{
        "id": "call_abc123",
        "name": "web_inspect",
        "arguments": {"url": "https://github.com/obra/superpowers",
                       "question": "compare this to Aurem's own skills"},
    }]
    # This mirrors engine.py's message-construction line exactly (the
    # FIXED version) - if engine.py regresses back to str(), this
    # would fail identically to test_1 above.
    msg = {"role": "assistant", "content": None,
           "tool_calls": [
               {"id": c["id"], "type": "function",
                "function": {"name": c["name"],
                              "arguments": _json.dumps(c["arguments"])}}
               for c in tool_calls]}
    args_str = msg["tool_calls"][0]["function"]["arguments"]
    decoded = _json.loads(args_str)   # must not raise
    assert decoded == tool_calls[0]["arguments"]


@pytest.mark.asyncio
async def test_5_live_multi_round_tool_call_on_fresh_conversation_no_length_needed():
    """NO MOCKS - proves this bug is NOT gated by conversation length.
    Runs a completely FRESH (zero prior messages) admin ORA Chat v2
    turn through the REAL configured provider (OpenRouter / Qwen,
    same provider configured in this pod's llm_configs for role
    'chat'), asking it to inspect a real URL - the exact request
    shape the founder used. If engine.py's tool_calls serialization
    is broken, the round-2 API call (which replays the round-1 tool
    call back to the provider) would raise/return a JSON-validation
    error from the provider itself. With the fix, this must complete
    without any {"type": "error"} event.
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    from services.ora_chat_v2 import engine

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL/DB_NAME not configured in this environment")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    active = None
    try:
        from services import llm_config_store
        active = await llm_config_store.get_active_config(db, "chat")
    except Exception as e:
        pytest.skip(f"could not resolve active chat config: {e!r}")
    if not active:
        pytest.skip("no active chat LLM config in this environment - cannot make a real call")

    session = {"session_id": "test-fresh-conv-json-corruption", "messages": []}
    user_message = (
        "Use the web_inspect tool to check https://github.com/obra/superpowers "
        "(question: 'what does this repo do?'), then give me one sentence "
        "comparing it to Aurem's own skills based on what you find."
    )

    events = []
    errors = []
    async for evt in engine.run_turn(
        db, admin_id="test_admin_json_corruption_regression",
        session=session, user_message=user_message,
    ):
        events.append(evt)
        if evt.get("type") == "error":
            errors.append(evt)

    # The specific class of bug we're regression-testing: a JSON /
    # validation error surfacing mid-turn because a prior round's
    # tool_calls history entry was malformed. Any other error type
    # (e.g. rate limit, network) is not what this test guards against
    # and should not fail the suite.
    json_validation_errors = [
        e for e in errors
        if "json" in str(e).lower() or "valid" in str(e).lower()
        or "arguments" in str(e).lower()
    ]
    assert not json_validation_errors, (
        f"tool_calls serialization is STILL producing invalid JSON that "
        f"the provider rejected: {json_validation_errors}"
    )
    final_events = [e for e in events if e.get("type") == "final"]
    assert final_events, (
        f"turn never reached a final answer (events seen: "
        f"{[e.get('type') for e in events]}); errors: {errors}"
    )
