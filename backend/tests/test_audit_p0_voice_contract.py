"""test_audit_p0_voice_contract.py — P0 audit fixes B3 + B5 (2026-11-25).

B3  worker _mode_d_e called fetch_file() with 6 args (stray httpx
    client first) vs the real 5-arg signature — every call raised
    TypeError, swallowed by `except: pass`, so Mode E audited ZERO
    file contents while reporting success. Proved here by running the
    REAL _mode_d_e with a 5-arg fake fetch_file and a fake GitHub
    tree: file contents must reach run_audit.
B5  BUSINESS_OWNER_VOICE_CONTRACT was appended on EVERY non-ora_panel
    turn, banning commit/push/deploy/repo/file/API from code tasks.
    Now gated by business_voice_applies() (routers/chat/misc.py) on
    the classify_intent() taxonomy the code already computes: C
    (code/build/deploy/fix/refactor), D (debug), E (audit) and
    loop-execution turns never get it.

Import stubs: routers/chat/* pull cto_services + half the service
layer at module top, none of which is in this seed tree. The stubs
below are the same pattern the #373 agent1 tests use (fake
cto_services.db) — minimal dummies for modules the code under test
never calls. Every stubbed name is listed next to the real import
it satisfies.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ── Stub modules (must exist BEFORE importing routers.chat.*) ───────
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
    auth.current_dev = _async_none
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
        "_detect_mode", "_deduct_tokens", "classify_intent",
        "_maybe_set_title", "_regenerate_without_recall",
        "_strip_council_block", "_persist_turn",
        "retrieved_context_for_grounding", "run_rendering_pipeline",
        "scan_findings_context_for_grounding",
        "_safe_provenance", "detect_prompt_injection",
        "is_fix_confirmation",
    ):
        setattr(ch, _name, (lambda *a, **k: "A")
                if _name == "classify_intent" else (lambda *a, **k: None))
    ch._persist_turn = _async_none
    ch.classify_intent = lambda *a, **k: "A"
    sys.modules.setdefault("services.chat_helpers", ch)

    # services.usage: is_founder_email (misc.py, stream.py)
    usage = types.ModuleType("services.usage")
    usage.is_founder_email = lambda *a, **k: False
    sys.modules.setdefault("services.usage", usage)

    # services.repo_context: get_repo_context (turn.py, stream.py)
    rc = types.ModuleType("services.repo_context")
    rc.get_repo_context = _async_empty_str
    sys.modules.setdefault("services.repo_context", rc)

    # services.user_response_sanitizer (turn.py, stream.py)
    urs = types.ModuleType("services.user_response_sanitizer")
    urs.sanitize_provider_for_user = lambda s, *a, **k: s
    urs.sanitize_provenance_for_user = lambda s, *a, **k: s
    sys.modules.setdefault("services.user_response_sanitizer", urs)

    # services.orchestrator is imported for REAL (turn.py / stream.py /
    # worker.py all do `from services.orchestrator import
    # chat_with_tools`). It only needs its three missing top-level
    # deps stubbed — same three as test_audit_p0_backend_fixes.py.
    # (A whole-module fake here would poison that file's B4/D2 tests,
    # which assert on the real module; pytest collects both files
    # before running either.)
    async def _async_list(*a, **k):
        return []

    tb = types.ModuleType("services.tools_bridge")
    tb.list_tools = _async_list
    tb.invoke_tool = _async_none
    tb.extract_tool_calls = lambda *a, **k: []
    tb.strip_tool_calls = lambda s: s
    tb.detect_unsourced_citations = lambda *a, **k: []
    sys.modules.setdefault("services.tools_bridge", tb)
    lt = types.ModuleType("services.local_tools")
    lt.TOOL_SPECS = []
    lt.invoke_local_tool = _async_none
    lt._is_safe_repo_path = lambda *a, **k: True
    sys.modules.setdefault("services.local_tools", lt)
    su = types.ModuleType("services.skill_usage")
    su.log_skill_use = lambda *a, **k: None
    sys.modules.setdefault("services.skill_usage", su)

    # services.subscription_tiers / loop_beta (turn.py panel fn)
    st = types.ModuleType("services.subscription_tiers")
    st.allowed_modes_for_tier = lambda tier: ["swift", "pro", "maxx"]
    sys.modules.setdefault("services.subscription_tiers", st)
    lb = types.ModuleType("services.loop_beta")
    lb.assert_maxx_daily_budget = _async_none
    sys.modules.setdefault("services.loop_beta", lb)

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
    sys.modules.setdefault("routers.chat.handoff_guard", hg)
    wd = types.ModuleType("routers.chat.watchdog")
    wd.consume_worker_queue = _async_none
    sys.modules.setdefault("routers.chat.watchdog", wd)
    rt = types.ModuleType("routers.chat.retries")
    rt.run_confidence_gate = _async_none
    sys.modules.setdefault("routers.chat.retries", rt)


_install_stubs()


async def _async_none(*a, **k):
    return None


from routers.chat.misc import (  # noqa: E402
    BUSINESS_OWNER_VOICE_CONTRACT, business_voice_applies,
)
from routers.chat.turn import (  # noqa: E402
    _chat_send_prepare_panel_and_resolve_mode,
)
from routers.chat.stream import (  # noqa: E402
    _chat_stream_setup_plain_english_panel,
)


# ── B5: the gate itself ──────────────────────────────────────────────
@pytest.mark.parametrize("recall_mode", ["A", "B", "F", None, ""])
def test_b5_gate_applies_to_non_technical_modes(recall_mode):
    """A (factual/greeting), B (advice), F (engage/marketing) and
    unclassified turns keep the business-owner voice."""
    assert business_voice_applies(recall_mode, "prompt") is True


@pytest.mark.parametrize("recall_mode", ["C", "D", "E"])
def test_b5_gate_blocks_technical_modes(recall_mode):
    """C (code/build/deploy/fix/refactor), D (debug), E (audit) never
    get the contract — those turns NEED the banned words."""
    assert business_voice_applies(recall_mode, "prompt") is False


def test_b5_gate_blocks_loop_execution_even_for_plain_prompt():
    """A loop turn is a technical task execution whatever the prompt
    classified as."""
    assert business_voice_applies("A", "loop") is False
    assert business_voice_applies(None, "loop") is False


def test_b5_contract_still_bans_the_words_it_always_banned():
    """The gate changed WHERE the contract applies, not WHAT it says."""
    assert "Never say: commit, push, deploy" in BUSINESS_OWNER_VOICE_CONTRACT
    assert "your main page" in BUSINESS_OWNER_VOICE_CONTRACT


def _fake_body(**overrides):
    d = {"ora_panel": False, "mode": "swift", "execution_mode": "prompt",
         "maxx_mode": False}
    d.update(overrides)
    return SimpleNamespace(**d)


# ── B5: /chat/send wiring ───────────────────────────────────────────
def _run_send_panel(recall_mode, **body_overrides):
    return asyncio.run(_chat_send_prepare_panel_and_resolve_mode(
        _fake_body(**body_overrides), {"tier": "free"}, None, "",
        recall_mode=recall_mode,
    ))


def test_b5_send_code_turn_gets_no_voice_contract():
    extra_sys = _run_send_panel("C")[0]
    assert BUSINESS_OWNER_VOICE_CONTRACT not in extra_sys


def test_b5_send_debug_and_audit_turns_get_no_voice_contract():
    for mode in ("D", "E"):
        assert BUSINESS_OWNER_VOICE_CONTRACT not in _run_send_panel(mode)[0]


def test_b5_send_advice_turn_keeps_voice_contract():
    assert BUSINESS_OWNER_VOICE_CONTRACT in _run_send_panel("B")[0]


def test_b5_send_unclassified_turn_keeps_old_behaviour():
    """Classification unavailable: contract applies (pre-fix behaviour)."""
    assert BUSINESS_OWNER_VOICE_CONTRACT in _run_send_panel(None)[0]


def test_b5_send_loop_turn_gets_no_voice_contract():
    assert BUSINESS_OWNER_VOICE_CONTRACT not in _run_send_panel(
        "A", execution_mode="loop")[0]


def test_b5_send_advisor_panel_never_gets_voice_contract():
    assert BUSINESS_OWNER_VOICE_CONTRACT not in _run_send_panel(
        "A", ora_panel=True)[0]


# ── B5: /chat/stream wiring ─────────────────────────────────────────
def _run_stream_panel(recall_mode, **body_overrides):
    return asyncio.run(_chat_stream_setup_plain_english_panel(
        0.0, recall_mode, 0, {"user_id": "u1", "tier": "free"}, "",
        _fake_body(**body_overrides), "u1",
    ))


def test_b5_stream_code_turn_gets_no_voice_contract():
    _, extra_sys = _run_stream_panel("C")
    assert BUSINESS_OWNER_VOICE_CONTRACT not in extra_sys


def test_b5_stream_advice_turn_keeps_voice_contract():
    _, extra_sys = _run_stream_panel("B")
    assert BUSINESS_OWNER_VOICE_CONTRACT in extra_sys


def test_b5_stream_loop_turn_gets_no_voice_contract():
    _, extra_sys = _run_stream_panel("A", execution_mode="loop")
    assert BUSINESS_OWNER_VOICE_CONTRACT not in extra_sys


# ── B3: _mode_d_e fetch_file arity ───────────────────────────────────
class _FakeResp:
    status_code = 200

    def __init__(self, tree):
        self._tree = tree

    def json(self):
        return {"tree": self._tree}


class _FakeHttpClient:
    """Stands in for httpx.AsyncClient (tree fetch only — the file
    reads go through fetch_file, which opens its own client)."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        return _FakeResp([
            {"type": "blob", "path": "backend/services/router.py"},
            {"type": "blob", "path": "backend/routers/chat/main.py"},
            {"type": "blob", "path": "README.md"},
        ])


class _FakeColl:
    def __init__(self, doc=None):
        self._doc = doc

    async def find_one(self, *a, **k):
        return self._doc

    async def update_one(self, *a, **k):
        return None


class _FakeDB:
    def __init__(self):
        self.cto_projects = _FakeColl({
            "github_owner": "AuremHQ", "github_repo": "Aurem",
            "branch": "main",
        })
        self.chat_sessions = _FakeColl()


def test_b3_mode_e_audit_reads_file_contents(monkeypatch):
    """Pre-fix this raised TypeError on the FIRST file (6 args into a
    5-arg function), swallowed by `except: pass`, so run_audit got
    file_blocks={} while still reporting success. Post-fix the two
    priority .py files arrive with real content."""
    import httpx

    fetch_calls = []
    audit_seen = {}

    async def _fake_fetch_file(owner, repo, path, ref, token):
        fetch_calls.append((owner, repo, path, ref, token))
        return f"# content of {path}\n"

    async def _fake_run_audit(**kwargs):
        audit_seen.update(kwargs)
        return {"report": "AUDIT-REPORT", "critical_count": 0,
                "high_count": 0}

    async def _fake_token(project):
        return ("fake-pat", None, None)

    gaw = types.ModuleType("services.github_api_writer")
    gaw.fetch_file = _fake_fetch_file
    sys.modules["services.github_api_writer"] = gaw
    mea = types.ModuleType("services.mode_e_auditor")
    mea.run_audit = _fake_run_audit
    sys.modules["services.mode_e_auditor"] = mea
    # _mode_d_e imports BOTH mode modules up front, even for Mode E.
    mdd = types.ModuleType("services.mode_d_debugger")
    mdd.run_debug_session = _async_none
    sys.modules["services.mode_d_debugger"] = mdd
    pv = types.ModuleType("services.pat_vault")
    pv.get_repo_token_or_error = _fake_token
    sys.modules["services.pat_vault"] = pv
    # NOTE: worker.py bound `get_db` at ITS import time (from-import),
    # so reassigning the stub module's attribute would not reach it —
    # patch the name on the worker module itself.
    import routers.chat.worker as _worker_mod
    monkeypatch.setattr(_worker_mod, "get_db", lambda: _FakeDB())
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpClient)

    from routers.chat.worker import _mode_d_e

    state = SimpleNamespace(
        body=SimpleNamespace(
            prompt="audit this repo", project_id="p1",
            session_id="s1", f12_payload=None,
        ),
        user_id="u1", q=None, activity={"label": ""},
    )
    result = asyncio.run(_mode_d_e(state, "E"))
    assert result["ok"] is True
    assert "AUDIT-REPORT" in result["content"]
    # The 5-arg call shape, twice, for the two priority .py files.
    assert fetch_calls == [
        ("AuremHQ", "Aurem", "backend/services/router.py", "main", "fake-pat"),
        ("AuremHQ", "Aurem", "backend/routers/chat/main.py", "main", "fake-pat"),
    ]
    # Contents (not just the path list) reached the auditor.
    assert audit_seen["file_blocks"] == {
        "backend/services/router.py": "# content of backend/services/router.py\n",
        "backend/routers/chat/main.py": "# content of backend/routers/chat/main.py\n",
    }
    assert len(audit_seen["file_tree"]) == 3
