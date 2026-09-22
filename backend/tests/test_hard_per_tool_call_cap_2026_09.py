"""
tests/test_hard_per_tool_call_cap_2026_09.py — Section 15.4 spec.

"Add a hard cap independent of the shared iteration budget: e.g. no
more than 3 calls to the exact same tool signature in one turn,
regardless of how much overall budget remains. This is the fix for
the 0%-success repeated-search_repo loop — debounce alone catches
identical calls, the cap catches a tool that keeps getting called
with slightly different useless arguments."

Root-cause context: the EXISTING `_consecutive_same_tool_no_progress`
guard only counts a CONSECUTIVE FAILURE streak — one incidental
success resets it to 0. These tests specifically mock every tool call
as a SUCCESS (ok=True) with DIFFERENT args each time, to prove the
new hard cap trips independently of that success/failure streak logic
— exactly the case the old guard would have missed forever.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.orchestrator import (
    _MAX_CALLS_PER_TOOL_PER_TURN,
    _tool_call_counts,
    chat_with_tools,
)


def test_tool_call_counts_counts_by_name_not_by_exact_args():
    invocations = [
        {"tool": "search_repo", "args": {"query": "a"}},
        {"tool": "search_repo", "args": {"query": "b"}},
        {"tool": "read_repo_file", "args": {"path": "x.py"}},
    ]
    counts = _tool_call_counts(invocations)
    assert counts["search_repo"] == 2
    assert counts["read_repo_file"] == 1


@pytest.mark.asyncio
async def test_hard_cap_trips_even_when_every_call_succeeds_with_different_args():
    """The exact 0%-success search_repo loop, but with a twist: every
    call SUCCEEDS (ok=True) — proving the hard cap is independent of
    the existing no-progress/failure-streak guard, which would never
    trip here since nothing ever fails."""
    call_n = {"n": 0}

    async def fake_llm(*args, **kwargs):
        call_n["n"] += 1
        return {
            "ok": True, "provider": "test-provider",
            "content": f"searching again, attempt {call_n['n']}",
            "fallback_chain": ["test-provider"], "mode": "chat",
        }

    def fake_extract_tool_calls(content):
        # A DIFFERENT query every time — never an exact repeat, so the
        # OLD debounce (_is_same_tool_call) never trips, and every
        # call is a distinct "useless" search_repo variant.
        n = call_n["n"]
        return [{"tool": "search_repo", "args": {"query": f"useless query {n}"}}]

    async def fake_invoke_tool(tool_name, args, **kwargs):
        # Always succeeds — the existing consecutive-no-progress guard
        # would reset to 0 on every single one of these.
        return {"ok": True, "result": "0 matches", "error": None}

    async def empty_upstream_list(jwt):
        return []

    with patch("services.orchestrator.call_llm_with_meta", new=AsyncMock(side_effect=fake_llm)), \
         patch("services.orchestrator.list_tools", new=AsyncMock(side_effect=empty_upstream_list)), \
         patch("services.orchestrator.extract_tool_calls", side_effect=fake_extract_tool_calls), \
         patch("services.orchestrator.invoke_tool", new=AsyncMock(side_effect=fake_invoke_tool)):
        result = await chat_with_tools(
            prompt="find every usage of foo() in the repo",
            jwt_token="fake", user_id="u1", project_id="p1",
            max_iters=10,  # generous budget — the CAP must trip well before this
        )

    tool_names = [inv.get("tool") for inv in result["tool_invocations"]]
    search_repo_count = tool_names.count("search_repo")

    assert search_repo_count <= _MAX_CALLS_PER_TOOL_PER_TURN, (
        f"hard cap did not trip: search_repo called {search_repo_count}x, "
        f"cap is {_MAX_CALLS_PER_TOOL_PER_TURN}"
    )
    assert result.get("hard_tool_cap_tripped") == "search_repo"
    assert result["turn_state"] == "partial"
    assert result["continue_available"] is True


@pytest.mark.asyncio
async def test_cap_does_not_trip_below_the_threshold():
    """Sanity: 2 distinct calls to the same tool (below the cap of 3)
    must complete normally, no false-positive trip."""
    call_n = {"n": 0}

    async def fake_llm(*args, **kwargs):
        call_n["n"] += 1
        if call_n["n"] <= 2:
            return {
                "ok": True, "provider": "test-provider",
                "content": f"searching, attempt {call_n['n']}",
                "fallback_chain": ["test-provider"], "mode": "chat",
            }
        return {
            "ok": True, "provider": "test-provider",
            "content": "Found it: foo() is defined in bar.py.",
            "fallback_chain": ["test-provider"], "mode": "chat",
        }

    def fake_extract_tool_calls(content):
        n = call_n["n"]
        if n <= 2:
            return [{"tool": "search_repo", "args": {"query": f"query {n}"}}]
        return []

    async def fake_invoke_tool(tool_name, args, **kwargs):
        return {"ok": True, "result": "1 match", "error": None}

    async def empty_upstream_list(jwt):
        return []

    with patch("services.orchestrator.call_llm_with_meta", new=AsyncMock(side_effect=fake_llm)), \
         patch("services.orchestrator.list_tools", new=AsyncMock(side_effect=empty_upstream_list)), \
         patch("services.orchestrator.extract_tool_calls", side_effect=fake_extract_tool_calls), \
         patch("services.orchestrator.invoke_tool", new=AsyncMock(side_effect=fake_invoke_tool)):
        result = await chat_with_tools(
            prompt="find foo()",
            jwt_token="fake", user_id="u1", project_id="p1",
            max_iters=10,
        )

    assert result.get("hard_tool_cap_tripped") is None
    tool_names = [inv.get("tool") for inv in result["tool_invocations"]]
    assert tool_names.count("search_repo") == 2
