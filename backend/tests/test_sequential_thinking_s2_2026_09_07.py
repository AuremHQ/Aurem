"""
tests/test_sequential_thinking_s2_2026_09_07.py — Master Plan
"BUILD QUALITY", Phase S2 (Sequential Thinking). Founder-specified
named tests:

    t_sequential_tool_registered
    t_sequential_used_on_complex
    t_sequential_not_used_on_trivial
    t_sequential_cost_logged

No external network call anywhere in this module (it's a local-only
tool) — nothing to mock.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from services import sequential_thinking_skills as st


# ── t_sequential_tool_registered ──────────────────────────────────────
def test_sequential_tool_registered():
    """sequential_thinking is callable — both directly and via the
    LOCAL_TOOLS dispatch table it's wired into, plus the Python-call
    known-tools set (tools_bridge) DeepSeek/GLM emissions rely on."""
    assert callable(st.sequential_thinking)
    assert "sequential_thinking" in st.SEQUENTIAL_THINKING_TOOLS

    from services.local_tools import LOCAL_TOOLS, TOOL_SPECS
    assert "sequential_thinking" in LOCAL_TOOLS
    assert LOCAL_TOOLS["sequential_thinking"] is st.sequential_thinking
    names = {s["name"] for s in TOOL_SPECS}
    assert "sequential_thinking" in names

    from services.tools_bridge import extract_tool_calls
    calls = extract_tool_calls(
        "sequential_thinking(thought='check the auth middleware first', "
        "thought_number=1, total_thoughts=3, next_thought_needed=True)"
    )
    assert any(c["tool"] == "sequential_thinking" for c in calls), (
        "sequential_thinking must be in tools_bridge's Python-call-syntax "
        "known-tools set, or DeepSeek/GLM's fn(...) emissions get dropped"
    )


# ── t_sequential_used_on_complex ────────────────────────────────────────
def test_sequential_used_on_complex():
    """A genuinely complex, multi-file / whole-system debugging ask
    gets sequential_thinking added to its tool catalog."""
    from core.tool_router import get_tools_for_task, is_complex_request

    msg = ("Debug this system — something is failing intermittently "
           "across multiple files and I can't find the root cause.")
    assert is_complex_request(msg) is True
    tools = get_tools_for_task(msg, "agentic")
    assert "sequential_thinking" in tools


# ── t_sequential_not_used_on_trivial ────────────────────────────────────
def test_sequential_not_used_on_trivial():
    """A trivial one-line ask must NOT get sequential_thinking — it
    would just burn tokens on a scratchpad the model doesn't need."""
    from core.tool_router import get_tools_for_task, is_complex_request

    msg = "change the button color to blue"
    assert is_complex_request(msg) is False
    tools = get_tools_for_task(msg, "agentic")
    assert "sequential_thinking" not in tools


def test_sequential_not_used_on_trivial_query_tier():
    """Same trivial-ness check on the query tier (e.g. 'what does this
    function do') — still no sequential_thinking."""
    from core.tool_router import get_tools_for_task

    tools = get_tools_for_task("what does this function do", "query")
    assert "sequential_thinking" not in tools


# ── t_sequential_cost_logged ────────────────────────────────────────────
def test_sequential_cost_logged():
    """Every call writes a lightweight row (thought_number,
    total_thoughts, is_revision, branch_id, next_thought_needed,
    cost_usd 0.0) — never the token/cost LLM shape."""
    captured = {}

    class _FakeCollection:
        async def insert_one(self, doc):
            captured.update(doc)

    class _FakeDB:
        sequential_thinking_calls = _FakeCollection()

    with patch("cto_services.db.get_db", return_value=_FakeDB()):
        asyncio.run(st.log_sequential_thinking_call(
            thought_number=2, total_thoughts=4, is_revision=True,
            branch_id="alt-hypothesis", next_thought_needed=True,
        ))
    assert captured["thought_number"] == 2
    assert captured["total_thoughts"] == 4
    assert captured["is_revision"] is True
    assert captured["branch_id"] == "alt-hypothesis"
    assert captured["next_thought_needed"] is True
    assert captured["cost_usd"] == 0.0
    assert "input_tokens" not in captured
    assert "output_tokens" not in captured


def test_sequential_cost_log_never_raises_on_db_failure():
    """Logging is best-effort — a DB failure must never propagate."""
    with patch("cto_services.db.get_db", side_effect=RuntimeError("db down")):
        asyncio.run(st.log_sequential_thinking_call(
            thought_number=1, total_thoughts=1, is_revision=False,
            branch_id=None, next_thought_needed=False,
        ))  # must not raise


# ── tool mechanics ───────────────────────────────────────────────────
def test_sequential_thinking_basic_call_shape():
    ctx = {}
    with patch("cto_services.db.get_db", return_value=None):
        out = asyncio.run(st.sequential_thinking(ctx, {
            "thought": "First check the auth middleware order",
            "thought_number": 1, "total_thoughts": 3,
            "next_thought_needed": True,
        }))
    assert out["ok"] is True
    assert out["thought_number"] == 1
    assert out["total_thoughts"] == 3
    assert out["next_thought_needed"] is True
    assert out["thought_history_length"] == 1
    assert ctx["sequential_thoughts"][0]["thought"] == "First check the auth middleware order"


def test_sequential_thinking_missing_thought_errors():
    ctx = {}
    out = asyncio.run(st.sequential_thinking(ctx, {"thought_number": 1}))
    assert out["ok"] is False
    assert "thought" in out["error"]


def test_sequential_thinking_revision_and_branch_tracked():
    """A revision + a branch both persist in the turn-scoped history
    and the branch shows up in the returned `branches` list."""
    ctx = {}
    with patch("cto_services.db.get_db", return_value=None):
        asyncio.run(st.sequential_thinking(ctx, {
            "thought": "assume it's the auth middleware",
            "thought_number": 1, "total_thoughts": 2,
            "next_thought_needed": True,
        }))
        revised = asyncio.run(st.sequential_thinking(ctx, {
            "thought": "actually it's the rate limiter, not auth",
            "thought_number": 2, "total_thoughts": 2,
            "is_revision": True, "revises_thought": 1,
            "next_thought_needed": True,
        }))
        branched = asyncio.run(st.sequential_thinking(ctx, {
            "thought": "alt path: check the DB connection pool instead",
            "thought_number": 3, "total_thoughts": 3,
            "branch_from_thought": 1, "branch_id": "db-pool-hypothesis",
            "next_thought_needed": False,
        }))
    assert revised["ok"] is True
    assert ctx["sequential_thoughts"][1]["is_revision"] is True
    assert ctx["sequential_thoughts"][1]["revises_thought"] == 1
    assert branched["ok"] is True
    assert "db-pool-hypothesis" in branched["branches"]
    assert branched["thought_history_length"] == 3


def test_sequential_thinking_is_local_only_no_network():
    """Sanity: the module never imports an HTTP client — this is a
    pure in-process scratchpad, no external service, no credentials."""
    import inspect
    src = inspect.getsource(st)
    assert "httpx" not in src
    assert "ext_client" not in src


# ── tools_bridge Shape-4 parser fix (found via real E2E run) ──────────
def test_sequential_thinking_thought_with_parens_still_parses():
    """2026-09-07 · Real E2E run against a live model surfaced a
    pre-existing tools_bridge bug: the old non-greedy Shape-4 regex
    truncated at the FIRST ')' anywhere in the call, including one
    inside a quoted string argument — silently dropping the call.
    sequential_thinking's free-text `thought` arg makes this
    non-hypothetical (any parenthetical aside in the model's own
    reasoning breaks it). Regression guard for the balanced-paren
    scanner fix."""
    from services.tools_bridge import extract_tool_calls

    text = (
        "sequential_thinking(thought=\"first check refresh() then "
        "cache.clear() — the race window is between those two calls "
        "(see services/feature_flags.py)\", thought_number=1, "
        "total_thoughts=2, next_thought_needed=True)\n"
        "sequential_thinking(thought=\"confirmed — fix the swap to be "
        "atomic\", thought_number=2, total_thoughts=2, "
        "is_revision=True, revises_thought=1, "
        "next_thought_needed=False)"
    )
    calls = [c for c in extract_tool_calls(text) if c["tool"] == "sequential_thinking"]
    assert len(calls) == 2
    assert calls[0]["args"]["thought_number"] == 1
    assert "cache.clear()" in calls[0]["args"]["thought"]
    assert calls[1]["args"]["is_revision"] is True
    assert calls[1]["args"]["revises_thought"] == 1


def test_multiple_known_tool_calls_with_parens_all_parse():
    """Broader regression: two DIFFERENT known tools back-to-back,
    one with a parenthetical in its string arg, both must parse —
    proves the fix isn't sequential_thinking-specific."""
    from services.tools_bridge import extract_tool_calls

    text = (
        "save_finding(text=\"found it (line 42) in auth.py\")\n"
        "read_repo_file(path='services/feature_flags.py')"
    )
    calls = extract_tool_calls(text)
    tools = {c["tool"] for c in calls}
    assert tools == {"save_finding", "read_repo_file"}
    finding = next(c for c in calls if c["tool"] == "save_finding")
    assert finding["args"]["text"] == "found it (line 42) in auth.py"
    read = next(c for c in calls if c["tool"] == "read_repo_file")
    assert read["args"]["path"] == "services/feature_flags.py"
