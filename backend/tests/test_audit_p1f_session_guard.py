"""test_audit_p1f_session_guard.py — P1F [project_id, session_id] guard.

Proves the P3 ownership guard from the ORA audit against REAL product
code (no live model, no Mongo, no GitHub network). Follows the
agent1/P0 repro-test pattern: fake the provider / DB at the boundary,
run the real function.

Guard under test: routers/chat/session_guard.py
  assert_session_belongs_to_project(db, session_id, project_id, user_id)
called at the top of POST /chat/send (turn.py) and POST /chat/stream
(stream.py), immediately after auth.

Rules proved here (see session_guard.py for the verify-first evidence):
  G1  session bound to another project          -> 403
  G2  session bound to the requesting project   -> passes
  G3  session with NO row (fresh client-minted) -> passes, NOT 403/500
      (frontend mints ids with crypto.randomUUID(); no server-side
      create endpoint exists — rejecting unknown ids would break
      every new chat. The first _persist_turn upsert binds the row
      to the requesting project via $setOnInsert.)
  G4  legacy row with no project_id             -> passes under any project
  G5  bound row + request project "home"/None   -> passes (no pair to
      contradict; the turn reads/appends only that session's project)
  G6  db None / session None                    -> passes (fail-open;
      _persist_turn is a no-op without a db/session anyway)
  G7  another USER's row with the same id       -> invisible to this
      user (query filters user_id), so treated as fresh (G3)

Wiring proved here (real chat_send / chat_stream, mock LLM):
  W1  /chat/send with another project's session -> HTTP 403, and the
      mock reply is never reached (guard is before the mock path)
  W2  /chat/send with own-project session       -> mock 200 reply
  W3  /chat/send with fresh (unknown) session   -> mock 200 reply
  W4  /chat/stream with another project's session -> HTTP 403
  W5  /chat/stream with own-project session     -> mock SSE reply
  W6  /chat/stream advisor (ora_panel=True) with another project's
      session -> HTTP 403 (advisor funnels through chat_stream)

Import stubs: routers/chat/* pull cto_services + half the service
layer at module top, none of which is in this seed tree. Same pattern
as test_audit_p0_voice_contract.py (fake cto_services.db etc.) —
minimal dummies for modules the code under test never calls. The
guard module itself imports only fastapi and needs no stub.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ── Fake Mongo (chat_sessions only) ──────────────────────────────────
class _FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, filt, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                return dict(d)
        return None


class _FakeDB:
    def __init__(self, docs):
        self.chat_sessions = _FakeCollection(docs)


def _db_with(*docs):
    return _FakeDB(list(docs))


# ── Stub modules (must exist BEFORE importing routers.chat.*) ────────
def _install_stubs():
    async def _async_none(*a, **k):
        return None

    async def _async_empty_str(*a, **k):
        return ""

    # cto_services.auth: current_dev (misc.py, turn.py, stream.py)
    cto = types.ModuleType("cto_services")
    cto.__path__ = []
    sys.modules.setdefault("cto_services", cto)
    auth = types.ModuleType("cto_services.auth")
    auth.current_dev = lambda authorization=None: _async_user()
    sys.modules.setdefault("cto_services.auth", auth)
    dbmod = types.ModuleType("cto_services.db")
    dbmod.get_db = lambda: None
    sys.modules.setdefault("cto_services.db", dbmod)

    # services.chat_helpers (misc.py / turn.py / stream.py / worker.py)
    ch = types.ModuleType("services.chat_helpers")
    for _name in (
        "_build_blocked_followup", "_build_done_fallback",
        "_build_failed_followup", "_generate_done_followup",
        "_generate_done_fallback",
        "_detect_mode", "_deduct_tokens",
        "_maybe_set_title", "_regenerate_without_recall",
        "_strip_council_block",
        "retrieved_context_for_grounding", "run_rendering_pipeline",
        "scan_findings_context_for_grounding",
        "_safe_provenance", "detect_prompt_injection",
        "is_fix_confirmation",
    ):
        setattr(ch, _name, (lambda *a, **k: None))
    ch._persist_turn = _async_none
    ch._maybe_set_title = _async_none
    ch._deduct_tokens = _async_none
    ch.classify_intent = lambda *a, **k: "A"
    # P1/B9 (folded tree): misc.py imports CHAT_TURNS_RETENTION from
    # chat_helpers at module level — the real value is 200
    # (services/chat_helpers.py:837), so the stub must provide it.
    ch.CHAT_TURNS_RETENTION = 200
    sys.modules.setdefault("services.chat_helpers", ch)

    # services.usage: is_founder_email (misc.py, stream.py);
    # assert_has_budget — stream.py imports it at module level
    # since P1/L1 (was a function-scope import inside chat_stream).
    usage = types.ModuleType("services.usage")
    usage.is_founder_email = lambda *a, **k: False
    usage.assert_has_budget = _async_none
    sys.modules.setdefault("services.usage", usage)

    # services.input_normalizer: ChatBody prompt validator (turn.py)
    inm = types.ModuleType("services.input_normalizer")
    inm.normalize_input = lambda text, *a, **k: {"text": text}
    sys.modules.setdefault("services.input_normalizer", inm)

    # services.repo_context: get_repo_context (turn.py, stream.py)
    rc = types.ModuleType("services.repo_context")
    rc.get_repo_context = _async_empty_str
    sys.modules.setdefault("services.repo_context", rc)

    # services.user_response_sanitizer (turn.py, stream.py)
    urs = types.ModuleType("services.user_response_sanitizer")
    urs.sanitize_provider_for_user = lambda s, *a, **k: s
    urs.sanitize_provenance_for_user = lambda s, *a, **k: s
    sys.modules.setdefault("services.user_response_sanitizer", urs)

    # services.orchestrator: stubbed (real one is not in the seed tree;
    # the mock path never calls it — the guard + mock return first)
    orch = types.ModuleType("services.orchestrator")
    orch.chat_with_tools = _async_none
    orch.build_timeout_message = lambda *a, **k: ("", None)
    sys.modules.setdefault("services.orchestrator", orch)

    # services.llm: call_emergent_watchdog (turn.py, stream.py)
    llm = types.ModuleType("services.llm")
    llm.call_emergent_watchdog = _async_none
    sys.modules.setdefault("services.llm", llm)

    # services.subscription_tiers / loop_beta (turn.py panel fn)
    st = types.ModuleType("services.subscription_tiers")
    st.allowed_modes_for_tier = lambda tier: ["swift", "pro", "maxx"]
    sys.modules.setdefault("services.subscription_tiers", st)
    lb = types.ModuleType("services.loop_beta")
    lb.assert_maxx_daily_budget = _async_none
    sys.modules.setdefault("services.loop_beta", lb)

    # Lazy imports inside the handlers (auth helper + mock check).
    ltl = types.ModuleType("services.loop_token_ledger")
    ltl.tag_request_surface = lambda *a, **k: None
    sys.modules.setdefault("services.loop_token_ledger", ltl)
    ic = types.ModuleType("services.interaction_contract")
    ic.request_conflict = lambda *a, **k: None
    ic.finalize = lambda *a, **k: None
    ic.new_request_id = lambda: "req-test-1"
    ic.enabled = lambda: False
    sys.modules.setdefault("services.interaction_contract", ic)
    en = types.ModuleType("services.engagement_nudge")
    en.maybe_fire_task_sent = _async_none
    sys.modules.setdefault("services.engagement_nudge", en)
    v2pkg = types.ModuleType("services.ora_chat_v2")
    v2pkg.__path__ = []
    sys.modules.setdefault("services.ora_chat_v2", v2pkg)
    v2lc = types.ModuleType("services.ora_chat_v2.llm_client")
    v2lc.is_mock = lambda: True  # wiring tests run the mock path
    sys.modules.setdefault("services.ora_chat_v2.llm_client", v2lc)

    # core.task_type: infer_task_type (turn.py, stream.py, worker.py)
    ctt = types.ModuleType("core.task_type")
    ctt.infer_task_type = lambda *a, **k: None
    sys.modules.setdefault("core.task_type", ctt)

    # routers.chat package: dummy `router` (misc.py `from . import router`)
    routers_pkg = types.ModuleType("routers")
    routers_pkg.__path__ = [str(BACKEND_DIR / "routers")]
    sys.modules.setdefault("routers", routers_pkg)

    class _DummyRouter:
        def __getattr__(self, name):
            def _decorator(*a, **k):
                def _wrap(fn):
                    return fn
                return _wrap
            return _decorator

    chat_pkg = types.ModuleType("routers.chat")
    chat_pkg.__path__ = [str(BACKEND_DIR / "routers" / "chat")]
    chat_pkg.router = _DummyRouter()
    sys.modules.setdefault("routers.chat", chat_pkg)

    # routers.chat.deps: get_current_user_db (misc.py)
    deps = types.ModuleType("routers.chat.deps")
    deps.get_current_user_db = _async_none
    sys.modules.setdefault("routers.chat.deps", deps)

    # routers.chat.handoff_guard / watchdog / retries (stream.py)
    hg = types.ModuleType("routers.chat.handoff_guard")
    hg._maybe_guard_shell_handoff_followup = _async_none
    hg._handoff_brief_is_shell_command = lambda *a, **k: False
    sys.modules.setdefault("routers.chat.handoff_guard", hg)
    wd = types.ModuleType("routers.chat.watchdog")
    wd.consume_worker_queue = _async_none
    sys.modules.setdefault("routers.chat.watchdog", wd)
    rt = types.ModuleType("routers.chat.retries")
    rt.run_confidence_gate = _async_none
    sys.modules.setdefault("routers.chat.retries", rt)


async def _async_user():
    return {"user_id": "u1", "tier": "free"}


_install_stubs()

from routers.chat.session_guard import (  # noqa: E402
    assert_session_belongs_to_project,
)
from routers.chat.turn import ChatBody, chat_send  # noqa: E402
from routers.chat import turn as turn_mod  # noqa: E402
from routers.chat import stream as stream_mod  # noqa: E402
from routers.chat.stream import chat_stream  # noqa: E402


# ── G1–G7: the guard itself ──────────────────────────────────────────
def test_g1_other_project_session_rejected_403():
    """A session bound to project-A, requested with project-B → 403."""
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(assert_session_belongs_to_project(
            db, session_id="s1", project_id="proj-b", user_id="u1"))
    assert ei.value.status_code == 403


def test_g2_own_project_session_passes():
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    asyncio.run(assert_session_belongs_to_project(
        db, session_id="s1", project_id="proj-a", user_id="u1"))


def test_g3_no_row_fresh_minted_session_passes():
    """No row = freshly client-minted id (crypto.randomUUID, no server
    create endpoint). Must NOT 403/500 — the first _persist_turn
    upsert binds it to the requesting project."""
    db = _db_with()
    asyncio.run(assert_session_belongs_to_project(
        db, session_id="brand-new-uuid", project_id="proj-a", user_id="u1"))


def test_g4_legacy_unbound_session_passes_under_any_project():
    """Pre-per-project rows have no project_id field; they belong to
    no project, so they cannot leak one project's turns into
    another's — allowed under proj-a AND proj-b AND home."""
    db = _db_with({"session_id": "s-old", "user_id": "u1"})
    for pid in ("proj-a", "proj-b", "home", None):
        asyncio.run(assert_session_belongs_to_project(
            db, session_id="s-old", project_id=pid, user_id="u1"))


def test_g5_bound_session_with_home_request_passes():
    """Bound row + missing/'home' request project: no pair to
    contradict — the turn only ever reads/appends that session's own
    project, no second project's context is loaded."""
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    for pid in ("home", None, "", "  "):
        asyncio.run(assert_session_belongs_to_project(
            db, session_id="s1", project_id=pid, user_id="u1"))


def test_g6_no_db_or_no_session_passes():
    asyncio.run(assert_session_belongs_to_project(
        None, session_id="s1", project_id="proj-b", user_id="u1"))
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    asyncio.run(assert_session_belongs_to_project(
        db, session_id=None, project_id="proj-b", user_id="u1"))
    asyncio.run(assert_session_belongs_to_project(
        db, session_id="", project_id="proj-b", user_id="u1"))


def test_g7_other_users_row_with_same_id_is_invisible():
    """The lookup filters user_id, so another user's row with the same
    session_id is never seen — treated as a fresh id (G3), never as
    a mismatch against the other user's project."""
    db = _db_with({"session_id": "s1", "user_id": "u2", "project_id": "proj-b"})
    asyncio.run(assert_session_belongs_to_project(
        db, session_id="s1", project_id="proj-a", user_id="u1"))


def test_g1_mismatch_message_names_no_project():
    """The 403 must not leak the other project's id."""
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(assert_session_belongs_to_project(
            db, session_id="s1", project_id="proj-b", user_id="u1"))
    assert "proj-a" not in str(ei.value.detail)
    assert "proj-b" not in str(ei.value.detail)


# ── W1–W6: wiring through the real handlers (mock LLM) ──────────────
def _send_body(session_id, project_id, **overrides):
    d = {"prompt": "hi", "session_id": session_id, "project_id": project_id}
    d.update(overrides)
    return ChatBody(**d)


def _run_send(monkeypatch, db, session_id, project_id, **overrides):
    monkeypatch.setattr(turn_mod, "get_db", lambda: db)
    body = _send_body(session_id, project_id, **overrides)
    return asyncio.run(chat_send(body, authorization=None))


def test_w1_send_other_project_session_403_before_mock(monkeypatch):
    """Cross-project session on /chat/send → 403, mock reply never
    reached (guard sits before the mock short-circuit)."""
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    with pytest.raises(HTTPException) as ei:
        _run_send(monkeypatch, db, "s1", "proj-b")
    assert ei.value.status_code == 403


def test_w2_send_own_project_session_reaches_mock(monkeypatch):
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    res = _run_send(monkeypatch, db, "s1", "proj-a")
    assert res["ok"] is True
    assert res["session_id"] == "s1"


def test_w3_send_fresh_session_reaches_mock(monkeypatch):
    """Fresh client-minted id on /chat/send: passes the guard and gets
    the normal mock reply — new chats are not broken."""
    db = _db_with()
    res = _run_send(monkeypatch, db, "brand-new-uuid", "proj-a")
    assert res["ok"] is True
    assert res["session_id"] == "brand-new-uuid"


def _run_stream(monkeypatch, db, session_id, project_id, **overrides):
    from starlette.requests import Request

    monkeypatch.setattr(stream_mod, "get_db", lambda: db)
    monkeypatch.setattr(turn_mod, "get_db", lambda: db)
    body = _send_body(session_id, project_id, **overrides)
    req = Request({"type": "http", "method": "POST",
                   "path": "/chat/stream", "headers": []})
    return asyncio.run(chat_stream(req, body, authorization=None))


async def _read_sse(resp):
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks).decode()


def test_w4_stream_other_project_session_403(monkeypatch):
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    with pytest.raises(HTTPException) as ei:
        _run_stream(monkeypatch, db, "s1", "proj-b")
    assert ei.value.status_code == 403


def test_w5_stream_own_project_session_reaches_mock_sse(monkeypatch):
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    resp = _run_stream(monkeypatch, db, "s1", "proj-a")
    text = asyncio.run(_read_sse(resp))
    assert "mock mode" in text


def test_w6_stream_advisor_other_project_session_403(monkeypatch):
    """Ask Advisor funnels through chat_stream (ora_panel=True) — the
    same guard must fire there, before the advisor worker runs."""
    db = _db_with({"session_id": "s1", "user_id": "u1", "project_id": "proj-a"})
    with pytest.raises(HTTPException) as ei:
        _run_stream(monkeypatch, db, "s1", "proj-b", ora_panel=True)
    assert ei.value.status_code == 403
