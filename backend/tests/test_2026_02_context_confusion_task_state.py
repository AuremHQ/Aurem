"""
tests/test_2026_02_context_confusion_task_state.py - Part A of the
architectural "Context Confusion" fix (2026-02).

services.task_state.get_current_task_state() is the read-only
current_task_state projection, in the exact shape the founder's
ticket specifies (task_id, task_description, pending_action_ref,
scope, created_at, updated_at). The DETERMINISTIC confirm-resolution
requirement itself ("yes, go ahead" resolves against an explicit
pending_action_ref, not by an LLM re-reading raw chat history") was
already built and default-on in services/actions/pending_action.py
(resolve_confirm(), pre-LLM, its own tests already cover that path).
This module is a thin, additive VIEW over it - these tests cover
that view's own contract only.
"""
import pytest
from unittest.mock import AsyncMock, patch

from services.task_state import get_current_task_state


@pytest.mark.asyncio
async def test_task_description_is_the_verbatim_current_prompt():
    with patch("services.actions.pending_action.get_active_actions", AsyncMock(return_value=[])):
        state = await get_current_task_state(
            db=object(), session_id="s1", user_id="u1",
            prompt="please update the footer text to say hello",
        )
    assert state["task_description"] == "please update the footer text to say hello"
    assert state["pending_action_ref"] is None
    assert state["scope"] == {"files": [], "allowed_operations": []}


@pytest.mark.asyncio
async def test_pending_action_ref_surfaces_the_active_actions_own_id():
    action = {
        "id": "abc123", "created_at": 1000.0,
        "payload": {"path": "frontend/src/components/Footer.jsx"},
    }
    with patch("services.actions.pending_action.get_active_actions", AsyncMock(return_value=[action])):
        state = await get_current_task_state(
            db=object(), session_id="s1", user_id="u1", prompt="yes, go ahead",
        )
    assert state["pending_action_ref"] == "abc123"
    assert state["task_id"] == "abc123"
    assert state["task_description"] == "yes, go ahead"     # verbatim, not re-inferred
    assert state["scope"]["files"] == ["frontend/src/components/Footer.jsx"]


@pytest.mark.asyncio
async def test_never_raises_when_the_lookup_itself_fails():
    with patch("services.actions.pending_action.get_active_actions", AsyncMock(side_effect=RuntimeError("db down"))):
        state = await get_current_task_state(
            db=object(), session_id="s1", user_id="u1", prompt="hi",
        )
    assert state["task_description"] == "hi"
    assert state["pending_action_ref"] is None


@pytest.mark.asyncio
async def test_no_session_no_db_still_returns_a_valid_shape():
    state = await get_current_task_state(db=None, session_id="", user_id="u1", prompt="hi")
    assert set(state.keys()) == {
        "task_id", "task_description", "pending_action_ref",
        "scope", "created_at", "updated_at",
    }
