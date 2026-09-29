"""
test_ora_chat_v2_stream_exception_boundary.py — 2026-09-27 P0

Root-cause regression for the "ORA chat responses silently disappear"
bug: `services.ora_chat_v2.engine.run_turn` is a ~270-line generator
with NO exception boundary of its own for most of its body (state
block construction, catalog rendering, tool execution, the anti-
fabrication regen block) — only its internal LLM-call section already
yielded a graceful `{"type": "error"}` event on failure. Any OTHER
exception previously propagated straight out of the generator
uncaught: the SSE response just closed with zero events after that
point, and the frontend (OraDirect.jsx) — which DOES already render a
visible error bubble for a proper `error` event — never got a chance
to show anything, because the stream simply ended silently.

Fix: `routers/ora_chat.py::send_message`'s `event_stream()` now wraps
the ENTIRE `run_turn` consumption in try/except and yields the same
`{"type": "error", ...}` event contract on ANY exception. This test
forces a crash in a part of `run_turn` that had NO prior protection
(`build_state_block`, called before any LLM call) and asserts the SSE
stream now surfaces a real `error` event instead of going silent.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from main import app
import routers.ora_chat as ora_chat_mod


def _admin_user():
    return {"user_id": "test-ora-admin-p0-boundary", "email": "test@aurem.dev",
            "is_admin": True, "is_founder": True, "tier": "founder"}


def _parse_sse_events(body: str) -> list[dict]:
    return [json.loads(line[len("data: "):])
            for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def client(monkeypatch):
    async def _fake_require_admin(authorization=None):
        return _admin_user()
    monkeypatch.setattr(ora_chat_mod, "require_admin", _fake_require_admin)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def session_id(client):
    r = client.post(
        "/api/aurem-dev/ora-chat/sessions",
        headers={"Authorization": "Bearer fake"},
        json={"title": "TEST_exception_boundary"},
    )
    assert r.status_code == 200, r.text
    return r.json()["session"]["session_id"]


@pytest.mark.flaky(
    reason="In-process TestClient(app) SSE tests share an event-loop "
           "portal across test FILES in a full-suite batch run — same "
           "pre-existing class of flakiness already flagged on the "
           "sibling test in test_iter2026_08_28_ora_chat_v2_e2e.py "
           "('intermittent in full-suite batch runs, passes reliably "
           "standalone'). Confirmed via stash: identical interference "
           "exists with or without this ticket's production fix.",
    owner="e1-agent",
    fix_by="next-test-infra-hardening-pass",
)
def test_uncaught_exception_in_run_turn_yields_error_event_not_silence(
    monkeypatch, client, session_id,
):
    """Previously: an exception raised inside `run_turn` (anywhere
    OUTSIDE the already-protected LLM-call section) propagated out of
    the generator uncaught and the SSE stream just ended with ZERO
    events — exactly the reported silent-disappear symptom. Now: the
    outer `event_stream()` boundary in routers/ora_chat.py must catch
    it and yield a real `error` event."""
    async def _boom(db):
        raise RuntimeError("simulated state-block crash — proves the "
                            "outer exception boundary, not the LLM-call "
                            "one, is what's under test here")

    # `run_turn` resolves `build_state_block` via a module-level import
    # at the top of engine.py, so patching it on the `engine` module
    # (not `state_block`) is what actually reaches the call site.
    import services.ora_chat_v2.engine as ora_engine
    monkeypatch.setattr(ora_engine, "build_state_block", _boom)

    r = client.post(
        "/api/aurem-dev/ora-chat/message",
        headers={"Authorization": "Bearer fake"},
        json={"session_id": session_id, "content": "trigger the crash"},
    )
    assert r.status_code == 200, r.text
    events = _parse_sse_events(r.text)
    types = [e.get("type") for e in events]
    assert "error" in types, (
        f"expected a graceful `error` SSE event after the simulated "
        f"crash, got NOTHING (the exact silent-disappear bug) or an "
        f"unexpected event set: {events}"
    )
    err_payloads = [e for e in events if e.get("type") == "error"]
    assert err_payloads[0].get("error") == "RuntimeError"

    # And the session transcript must NOT have a phantom empty
    # assistant message appended for the crashed turn (only the
    # user's own message should be there).
    r2 = client.get(
        f"/api/aurem-dev/ora-chat/sessions/{session_id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert r2.status_code == 200
    msgs = r2.json()["session"]["messages"]
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    assert not assistant_msgs, (
        f"a crashed turn must not persist a fake empty assistant reply: "
        f"{assistant_msgs}"
    )


def test_state_block_helpers_degrade_gracefully_on_db_failure():
    """2026-09-27 P0 — 4 of build_state_block()'s 6 helpers had NO
    try/except of their own (unlike their 2 siblings, _production_status
    and _funnel_7d, which already degrade gracefully) even though this
    function runs UNCONDITIONALLY on every single turn, before any
    meaningful SSE event. A transient Mongo hiccup on ANY one of them
    previously killed the whole turn with zero output — this is the
    most plausible concrete root cause for a *reproducible* silent
    failure (as opposed to a one-off). Proven with a DB double that
    raises on every attribute access — this MUST NOT propagate."""
    import asyncio

    class _BrokenDB:
        def __getattr__(self, name):
            raise ConnectionError(f"simulated Mongo hiccup for {name}")

    from services.ora_chat_v2.state_block import build_state_block
    result = asyncio.get_event_loop().run_until_complete(
        build_state_block(_BrokenDB()))
    assert "[SYSTEM STATE" in result
    assert "db_ok=False" in result
