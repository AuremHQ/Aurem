"""test_audit_p1_latency_fixes.py — P1 audit latency fixes (2026-11-25).

Proves each P1 latency fix from the ORA audit against REAL product
code (no live model, no Mongo, no GitHub network). Follows the
agent1/agent2 repro-test pattern: fake the provider / DB at the
boundary, run the real function.

Fixes covered:
  L1  pre-flight awaits run in two concurrent waves (was: 12
      sequential awaits in chat_stream); the brain fetch is split out
      so it runs with the repo-context build, and it reuses bin_ctx
      (owner/repo/PAT) instead of a second find_one + PAT lookup.
      Council retrieval + knowledge injection, and the plain-English
      flag check + house-rules pair, run concurrently too.
  L2  true-streaming OpenRouter transport (stream_openrouter_model:
      provider-sized SSE deltas, no fixed re-chunking, no sleep);
      the main-path drip loop emits 512-char frames with no pacing
      sleep (was: 6 chars / 12 ms). The retry-replay path is
      deliberately UNCHANGED (16 chars / 5 ms).
  L3  shared pooled HTTP clients (services/llm/_http.py): one
      httpx.AsyncClient per timeout, one AsyncOpenAI per
      (base_url, api_key, timeout, headers), one AsyncGroq per
      (api_key, timeout) — created once, reused, never closed by
      callers.

Import stubs: same pattern as test_audit_p0_voice_contract.py
(fake cto_services.db, chat_helpers, orchestrator deps, routers.chat
package), plus fakes for the modules the L1 helpers import at call
time (project_brain, pat_vault, ora_council_retriever,
knowledge_injector, feature_flags, house_rules). Every stubbed name
is listed next to the real import it satisfies. Production code is
never stubbed — only the world around it.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ── Stub modules (must exist BEFORE importing routers.chat.*) ───────
# Copied from test_audit_p0_voice_contract.py (same stub set, so both
# files can run in one pytest process), plus the L1 call-time imports.
def _real_or_stub(name, fake):
    """Install `fake` for `name`, with any attribute NOT defined on
    the fake delegating to the REAL module (same fix as
    tests/test_part15_admin_fixes.py). The tests in this file depend
    on the fake behaviours, so the fake must win for its defined
    attributes; anything else falls through to the real module so
    `from <name> import <other>` keeps working for every other test
    file collected after this one. A plain real-first import broke
    this file's own tests (they need the fake behaviours); a bare
    fake broke other files' imports.
    """
    _real = [None]

    def __getattr__(attr, _name=name, _real=_real):
        if _real[0] is None:
            saved = sys.modules.pop(_name, None)
            try:
                _real[0] = importlib.import_module(_name)
            finally:
                if saved is not None:
                    sys.modules[_name] = saved
        return getattr(_real[0], attr)

    fake.__getattr__ = __getattr__
    sys.modules[name] = fake


def _install_stubs():
    async def _async_none(*a, **k):
        return None

    async def _async_empty_str(*a, **k):
        return ""

    # cto_services.auth: current_dev (misc.py, turn.py, stream.py)
    cto = types.ModuleType("cto_services")
    # Real __path__ (not []) so the _real_or_stub __getattr__
    # delegation can load the real cto_services.* submodule.
    # An empty __path__ made `importlib.import_module` fail
    # with ModuleNotFoundError when a fake submodule delegated.
    cto.__path__ = [str(BACKEND_DIR / "cto_services")]
    _real_or_stub("cto_services", cto)
    auth = types.ModuleType("cto_services.auth")
    auth.current_dev = _async_none
    _real_or_stub("cto_services.auth", auth)
    dbmod = types.ModuleType("cto_services.db")
    dbmod.get_db = lambda: None
    _real_or_stub("cto_services.db", dbmod)

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
    # P1/B9 (folded tree): misc.py imports CHAT_TURNS_RETENTION from
    # chat_helpers at module level — the real value is 200
    # (services/chat_helpers.py:837), so the stub must provide it.
    ch.CHAT_TURNS_RETENTION = 200
    _real_or_stub("services.chat_helpers", ch)

    # services.usage: is_founder_email (misc.py, stream.py);
    # assert_has_budget — stream.py imports it at module level
    # since P1/L1 (was a function-scope import inside chat_stream).
    usage = types.ModuleType("services.usage")
    usage.is_founder_email = lambda *a, **k: False
    usage.assert_has_budget = _async_none
    _real_or_stub("services.usage", usage)

    # services.repo_context: get_repo_context (turn.py, stream.py)
    rc = types.ModuleType("services.repo_context")
    rc.get_repo_context = _async_empty_str
    _real_or_stub("services.repo_context", rc)

    # services.user_response_sanitizer (turn.py, stream.py)
    urs = types.ModuleType("services.user_response_sanitizer")
    urs.sanitize_provider_for_user = lambda s, *a, **k: s
    urs.sanitize_provenance_for_user = lambda s, *a, **k: s
    _real_or_stub("services.user_response_sanitizer", urs)

    # services.orchestrator is imported for REAL (turn.py / stream.py /
    # worker.py all do `from services.orchestrator import
    # chat_with_tools`). It only needs its three missing top-level
    # deps stubbed — same three as test_audit_p0_backend_fixes.py.
    async def _async_list(*a, **k):
        return []

    tb = types.ModuleType("services.tools_bridge")
    tb.list_tools = _async_list
    tb.invoke_tool = _async_none
    tb.extract_tool_calls = lambda *a, **k: []
    tb.strip_tool_calls = lambda s: s
    tb.detect_unsourced_citations = lambda *a, **k: []
    _real_or_stub("services.tools_bridge", tb)
    lt = types.ModuleType("services.local_tools")
    lt.TOOL_SPECS = []
    lt.invoke_local_tool = _async_none
    lt._is_safe_repo_path = lambda *a, **k: True
    _real_or_stub("services.local_tools", lt)
    su = types.ModuleType("services.skill_usage")
    su.log_skill_use = lambda *a, **k: None
    _real_or_stub("services.skill_usage", su)

    # services.subscription_tiers / loop_beta (turn.py panel fn)
    st = types.ModuleType("services.subscription_tiers")
    st.allowed_modes_for_tier = lambda tier: ["swift", "pro", "maxx"]
    _real_or_stub("services.subscription_tiers", st)
    lb = types.ModuleType("services.loop_beta")
    lb.assert_maxx_daily_budget = _async_none
    _real_or_stub("services.loop_beta", lb)

    # core.task_type: infer_task_type (turn.py, stream.py, worker.py)
    ctt = types.ModuleType("core.task_type")
    ctt.infer_task_type = lambda *a, **k: None
    _real_or_stub("core.task_type", ctt)

    # routers.chat package: dummy `router` (misc.py `from . import router`)
    routers_pkg = types.ModuleType("routers")
    routers_pkg.__path__ = [str(BACKEND_DIR / "routers")]
    _real_or_stub("routers", routers_pkg)

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
    _real_or_stub("routers.chat", chat_pkg)

    # routers.chat.deps: get_current_user_db (misc.py)
    deps = types.ModuleType("routers.chat.deps")
    deps.get_current_user_db = _async_none
    _real_or_stub("routers.chat.deps", deps)

    # routers.chat.handoff_guard / watchdog / retries (stream.py)
    hg = types.ModuleType("routers.chat.handoff_guard")
    hg._maybe_guard_shell_handoff_followup = _async_none
    _real_or_stub("routers.chat.handoff_guard", hg)
    wd = types.ModuleType("routers.chat.watchdog")
    wd.consume_worker_queue = _async_none
    _real_or_stub("routers.chat.watchdog", wd)
    rt = types.ModuleType("routers.chat.retries")
    rt.run_confidence_gate = _async_none
    _real_or_stub("routers.chat.retries", rt)

    # ── L1 call-time imports (fakes; tests monkeypatch their attrs) ──
    # services.project_brain: get_brain_context (brain fetch)
    pb = types.ModuleType("services.project_brain")
    pb.get_brain_context = _async_empty_str
    _real_or_stub("services.project_brain", pb)
    # services.pat_vault: get_repo_token_or_error (brain fetch fallback)
    pv = types.ModuleType("services.pat_vault")
    pv.get_repo_token_or_error = _async_none
    _real_or_stub("services.pat_vault", pv)
    # services.ora_council_retriever: get_council_few_shot (ora panel)
    ocr = types.ModuleType("services.ora_council_retriever")

    async def _async_empty_tuple(*a, **k):
        return ("", 0)

    ocr.get_council_few_shot = _async_empty_tuple
    _real_or_stub("services.ora_council_retriever", ocr)
    # services.knowledge_injector: build_answer_context (ora panel)
    ki = types.ModuleType("services.knowledge_injector")

    async def _async_empty_pair(*a, **k):
        return ("", [])

    ki.build_answer_context = _async_empty_pair
    _real_or_stub("services.knowledge_injector", ki)
    # services.feature_flags: is_enabled (plain-English flag)
    ff = types.ModuleType("services.feature_flags")
    ff.is_enabled = _async_none
    _real_or_stub("services.feature_flags", ff)
    # services.house_rules: get_active_house_rules /
    # format_house_rules_block / get_active_chat_prompt (plain English)
    hr = types.ModuleType("services.house_rules")
    hr.get_active_house_rules = _async_none
    hr.format_house_rules_block = lambda s: s or ""
    hr.get_active_chat_prompt = _async_none
    _real_or_stub("services.house_rules", hr)


_install_stubs()


import routers.chat.stream as stream_mod  # noqa: E402
from routers.chat.stream import (  # noqa: E402
    _chat_stream_fetch_brain_context,
    _chat_stream_run_preflight,
    _chat_stream_setup_ora_panel,
    _chat_stream_setup_plain_english_panel,
    _emit_content_tokens,
    _gather_preflight,
    _replay_retry_gen,
)
from routers.chat.misc import (  # noqa: E402
    BUSINESS_OWNER_VOICE_CONTRACT,
    PLAIN_ENGLISH_EXPLAIN_CONTRACT,
)


def _fake_body(**overrides):
    d = {"prompt": "hi", "session_id": "s1", "project_id": "p1",
         "execution_mode": "prompt", "agent": "auto", "ora_panel": False,
         "mode": "swift", "f12_payload": None}
    d.update(overrides)
    return SimpleNamespace(**d)


def _fake_user(**overrides):
    d = {"user_id": "u1", "tier": "free", "email": "a@b.c"}
    d.update(overrides)
    return d


class _FakeCollection:
    """Minimal Motor-like collection: find_one returns a preset doc
    (or raises, to prove it was never called)."""

    def __init__(self, doc=None, explode=False):
        self.doc = doc
        self.explode = explode
        self.find_one_calls = []

    async def find_one(self, *args, **kwargs):
        self.find_one_calls.append((args, kwargs))
        if self.explode:
            raise AssertionError("find_one must not be called on this path")
        return self.doc


class _FakeDB:
    def __init__(self, project_doc=None, explode_find_one=False):
        self.cto_projects = _FakeCollection(
            doc=project_doc, explode=explode_find_one)


# ── L1: pre-flight waves run concurrently ────────────────────────────
def _timed_fake(monkeypatch, name, delay, result, calls):
    """An async fake that sleeps `delay`, records (start, end), and
    returns `result`. `calls` is a dict the fake appends to."""
    async def _fake(*a, **k):
        start = time.monotonic()
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            calls[name] = (start, time.monotonic(), "cancelled")
            raise
        calls[name] = (start, time.monotonic(), "ok")
        return result
    return _fake


def test_l1_preflight_waves_run_concurrently(monkeypatch):
    """The real _chat_stream_run_preflight with 0.25 s wave-1 fakes +
    0.2 s wave-2 fakes must finish in ~0.45 s, not the ~1.35 s the
    old sequential awaits took — and all three wave-1 tasks must
    overlap in time (max start < min end)."""
    calls: dict = {}
    import time as _time_mod

    async def _fake_stream_ctx(*a, **k):
        return ("jwt", "u1", _time_mod, _time_mod.monotonic(),
                _FakeDB(), "p1", None)

    async def _fake_safe_factory():
        async def _safe(coro, label, timeout_s=12.0):
            return await coro
        return _safe

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(stream_mod, "_chat_stream_setup_stream_context", _fake_stream_ctx)
    monkeypatch.setattr(stream_mod, "_chat_stream_define_safe", _fake_safe_factory)
    monkeypatch.setattr(stream_mod, "_chat_stream_setup_ora_agent", _noop)
    monkeypatch.setattr(stream_mod, "_chat_stream_handle_loop_mode", _noop)
    monkeypatch.setattr(stream_mod, "assert_has_budget",
                        _timed_fake(monkeypatch, "budget", 0.25, None, calls))
    monkeypatch.setattr(stream_mod, "_chat_stream_check_injection_and_founder",
                        _timed_fake(monkeypatch, "injection", 0.25, True, calls))
    monkeypatch.setattr(stream_mod, "_chat_stream_setup_project_context",
                        _timed_fake(monkeypatch, "project", 0.25,
                                    SimpleNamespace(repo_owner="o", repo_name="r", pat="tok"), calls))
    monkeypatch.setattr(stream_mod, "_chat_stream_prepare_repo_context",
                        _timed_fake(monkeypatch, "repo", 0.2, ("REPO", ""), calls))
    monkeypatch.setattr(stream_mod, "_chat_stream_fetch_brain_context",
                        _timed_fake(monkeypatch, "brain", 0.2, "[PROJECT MEMORY]\nBRAIN", calls))
    monkeypatch.setattr(stream_mod, "_chat_stream_guard_shell_handoff",
                        _timed_fake(monkeypatch, "guard", 0.2, None, calls))

    t0 = time.monotonic()
    pre = asyncio.run(_chat_stream_run_preflight(
        _fake_body(), _fake_user(), SimpleNamespace(),
        "Bearer x", time.monotonic(),
    ))
    wall = time.monotonic() - t0

    assert wall < 0.70, f"pre-flight took {wall:.2f}s — waves did not run concurrently"
    # Wave 1 overlap: every task started before any task finished.
    w1 = [calls[k] for k in ("budget", "injection", "project")]
    assert max(s for s, _, _ in w1) < min(e for _, e, _ in w1), (
        f"wave-1 tasks did not overlap: {w1}")
    w2 = [calls[k] for k in ("repo", "brain", "guard")]
    assert max(s for s, _, _ in w2) < min(e for _, e, _ in w2), (
        f"wave-2 tasks did not overlap: {w2}")
    # Values still flow through exactly.
    assert pre["is_founder"] is True
    assert pre["bin_ctx"].pat == "tok"
    assert pre["repo_ctx"] == "REPO" and pre["url_ctx"] == ""
    assert pre["brain_ctx"] == "[PROJECT MEMORY]\nBRAIN"
    assert pre["clarify_text"] is None
    assert pre["user_id"] == "u1" and pre["pid_stream"] == "p1"
    assert pre["jwt_token"] == "jwt"


def test_l1_sequential_fakes_take_the_full_sum(monkeypatch):
    """Negative control for the timing test above: the SAME fakes,
    awaited one by one, take >= 1.30 s — so the < 0.70 s bound in
    test_l1_preflight_waves_run_concurrently is a real discriminator,
    not a vacuous pass."""
    calls: dict = {}
    fakes = [
        _timed_fake(monkeypatch, "a", 0.25, None, calls),
        _timed_fake(monkeypatch, "b", 0.25, None, calls),
        _timed_fake(monkeypatch, "c", 0.25, None, calls),
        _timed_fake(monkeypatch, "d", 0.2, None, calls),
        _timed_fake(monkeypatch, "e", 0.2, None, calls),
        _timed_fake(monkeypatch, "f", 0.2, None, calls),
    ]

    async def _run_sequential():
        for f in fakes:
            await f()

    t0 = time.monotonic()
    asyncio.run(_run_sequential())
    assert time.monotonic() - t0 >= 1.30


def test_l1_wave1_fail_fast_cancels_siblings(monkeypatch):
    """When the budget gate fails immediately, a slow sibling must be
    cancelled — the turn must refuse in < 1 s, not wait out the
    sibling's 5 s sleep (the old sequential code also refused
    immediately; gather must not regress that)."""
    from fastapi import HTTPException

    calls: dict = {}

    async def _fast_budget(*a, **k):
        raise HTTPException(402, "no budget")

    async def _slow_injection(*a, **k):
        start = time.monotonic()
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            calls["injection"] = (start, time.monotonic(), "cancelled")
            raise
        calls["injection"] = (start, time.monotonic(), "ok")
        return True

    async def _quick_project(*a, **k):
        return None

    async def _noop(*a, **k):
        return None

    import time as _time_mod

    async def _fake_stream_ctx(*a, **k):
        return ("jwt", "u1", _time_mod, _time_mod.monotonic(),
                _FakeDB(), "p1", None)

    async def _fake_safe_factory():
        async def _safe(coro, label, timeout_s=12.0):
            return await coro
        return _safe

    monkeypatch.setattr(stream_mod, "_chat_stream_setup_stream_context", _fake_stream_ctx)
    monkeypatch.setattr(stream_mod, "_chat_stream_define_safe", _fake_safe_factory)
    monkeypatch.setattr(stream_mod, "_chat_stream_setup_ora_agent", _noop)
    monkeypatch.setattr(stream_mod, "assert_has_budget", _fast_budget)
    monkeypatch.setattr(stream_mod, "_chat_stream_check_injection_and_founder", _slow_injection)
    monkeypatch.setattr(stream_mod, "_chat_stream_setup_project_context", _quick_project)

    t0 = time.monotonic()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(_chat_stream_run_preflight(
            _fake_body(), _fake_user(), SimpleNamespace(),
            "Bearer x", time.monotonic(),
        ))
    wall = time.monotonic() - t0
    assert ei.value.status_code == 402
    assert wall < 1.0, f"fail-fast took {wall:.2f}s — sibling was not cancelled"
    assert calls["injection"][2] == "cancelled"


# ── L1: brain fetch reuses bin_ctx (dedupe) ──────────────────────────
def test_l1_brain_fetch_reuses_bin_ctx_no_second_find_one(monkeypatch):
    """With bin_ctx present, the brain fetch must NOT touch
    cto_projects.find_one or the PAT vault at all — both already ran
    in build_bin_context this request. Any call raises, so a
    regression fails loudly instead of silently re-reading."""
    import routers.chat.stream as _sm

    db = _FakeDB(explode_find_one=True)
    monkeypatch.setattr(_sm, "get_db", lambda: db)
    pv = sys.modules["services.pat_vault"]

    async def _exploding_pat(*a, **k):
        raise AssertionError("PAT lookup must not run when bin_ctx is present")

    monkeypatch.setattr(pv, "get_repo_token_or_error", _exploding_pat)
    pb = sys.modules["services.project_brain"]
    seen = {}

    async def _fake_brain(db_, project_id, repo_full, github_token=None):
        seen["repo_full"] = repo_full
        seen["token"] = github_token
        seen["project_id"] = project_id
        return "BRAIN-BLOB"

    monkeypatch.setattr(pb, "get_brain_context", _fake_brain)

    async def _run():
        _safe = await _sm._chat_stream_define_safe()
        return await _chat_stream_fetch_brain_context(
            _fake_body(), "u1", _safe,
            bin_ctx=SimpleNamespace(repo_owner="acme", repo_name="web", pat="tok123"),
        )

    assert asyncio.run(_run()) == "[PROJECT MEMORY]\nBRAIN-BLOB"
    assert seen == {"repo_full": "acme/web", "token": "tok123", "project_id": "p1"}
    assert db.cto_projects.find_one_calls == []


def test_l1_brain_fetch_falls_back_without_bin_ctx(monkeypatch):
    """Without bin_ctx the original find_one + PAT path still runs —
    the dedupe is an optimisation, not a behaviour change."""
    import routers.chat.stream as _sm

    proj = {"github_owner": "acme", "github_repo": "web",
            "github_token": "ct", "auth_method": "pat",
            "installation_id": None, "user_id": "u1"}
    db = _FakeDB(project_doc=proj)
    monkeypatch.setattr(_sm, "get_db", lambda: db)
    pv = sys.modules["services.pat_vault"]
    pat_seen = {}

    async def _fake_pat(doc):
        pat_seen["doc"] = doc
        return ("tokABC", None, None)

    monkeypatch.setattr(pv, "get_repo_token_or_error", _fake_pat)
    pb = sys.modules["services.project_brain"]
    seen = {}

    async def _fake_brain(db_, project_id, repo_full, github_token=None):
        seen["repo_full"] = repo_full
        seen["token"] = github_token
        return "B"

    monkeypatch.setattr(pb, "get_brain_context", _fake_brain)

    async def _run():
        _safe = await _sm._chat_stream_define_safe()
        return await _chat_stream_fetch_brain_context(
            _fake_body(), "u1", _safe, bin_ctx=None)

    assert asyncio.run(_run()) == "[PROJECT MEMORY]\nB"
    assert len(db.cto_projects.find_one_calls) == 1
    assert db.cto_projects.find_one_calls[0][0][0] == {
        "project_id": "p1", "user_id": "u1"}
    assert pat_seen["doc"] is proj
    assert seen == {"repo_full": "acme/web", "token": "tokABC"}


def test_l1_brain_fetch_no_project_returns_empty_without_db(monkeypatch):
    """Home chat (no project): empty brain context, zero DB traffic."""
    import routers.chat.stream as _sm

    monkeypatch.setattr(_sm, "get_db",
                        lambda: (_ for _ in ()).throw(AssertionError("db must not be touched")))

    async def _run():
        _safe = await _sm._chat_stream_define_safe()
        return await _chat_stream_fetch_brain_context(
            _fake_body(project_id=""), "u1", _safe, bin_ctx=None)

    assert asyncio.run(_run()) == ""


# ── L1: ora-panel council + knowledge run concurrently ──────────────
def test_l1_ora_panel_runs_council_and_knowledge_concurrently(monkeypatch):
    """Council retrieval + knowledge injection (0.25 s each) finish
    in < 0.40 s together, and the composed extra_sys keeps the old
    order: knowledge FIRST, council second, incoming base third."""
    ocr = sys.modules["services.ora_council_retriever"]
    ki = sys.modules["services.knowledge_injector"]
    dbmod = sys.modules["cto_services.db"]
    monkeypatch.setattr(dbmod, "get_db", lambda: _FakeDB())

    async def _slow_council(*a, **k):
        await asyncio.sleep(0.3)
        return ("COUNCIL", 2)

    async def _slow_knowledge(*a, **k):
        await asyncio.sleep(0.3)
        return ("KNOW", [])

    monkeypatch.setattr(ocr, "get_council_few_shot", _slow_council)
    monkeypatch.setattr(ki, "build_answer_context", _slow_knowledge)

    t0 = time.monotonic()
    mode, block, recalled, extra = asyncio.run(
        _chat_stream_setup_ora_panel(
            _fake_body(), _fake_user(), "BASE", None, "", 0))
    wall = time.monotonic() - t0

    assert wall < 0.45, f"ora-panel took {wall:.2f}s — council/knowledge ran sequentially"
    assert mode == "A"  # stubbed classify_intent returns "A"
    assert block == "COUNCIL" and recalled == 2
    assert extra == "KNOW\n\nCOUNCIL\n\nBASE"


def test_l1_ora_panel_council_failure_still_runs_knowledge(monkeypatch):
    """A council failure degrades to empty (as before) and must not
    take the knowledge block down with it — nor change recall_mode."""
    ocr = sys.modules["services.ora_council_retriever"]
    ki = sys.modules["services.knowledge_injector"]
    dbmod = sys.modules["cto_services.db"]
    monkeypatch.setattr(dbmod, "get_db", lambda: _FakeDB())

    async def _failing_council(*a, **k):
        raise RuntimeError("index blew up")

    async def _knowledge(*a, **k):
        return ("KNOW", [])

    monkeypatch.setattr(ocr, "get_council_few_shot", _failing_council)
    monkeypatch.setattr(ki, "build_answer_context", _knowledge)

    mode, block, recalled, extra = asyncio.run(
        _chat_stream_setup_ora_panel(
            _fake_body(), _fake_user(), "BASE", None, "", 0))
    assert mode == "A" and block == "" and recalled == 0
    assert extra == "KNOW\n\nBASE"


# ── L1: plain-English flag runs with the house-rules pair ───────────
def test_l1_plain_english_flag_runs_with_house_rules(monkeypatch):
    """Flag check (0.25 s) + house-rules pair (0.25 s + 0.2 s
    sequential inside) finish in < 0.55 s together, and the composed
    extra_sys keeps the old order: chat-prompt, house-rules, base,
    plain-English, voice contract."""
    ff = sys.modules["services.feature_flags"]
    hr = sys.modules["services.house_rules"]

    async def _slow_flag(*a, **k):
        await asyncio.sleep(0.3)
        return True

    async def _slow_rules(*a, **k):
        await asyncio.sleep(0.3)
        return "HR"

    async def _slow_chat_prompt(*a, **k):
        await asyncio.sleep(0.2)
        return "CHAT"

    monkeypatch.setattr(ff, "is_enabled", _slow_flag)
    monkeypatch.setattr(hr, "get_active_house_rules", _slow_rules)
    monkeypatch.setattr(hr, "format_house_rules_block", lambda s: f"[HR]{s}[/HR]")
    monkeypatch.setattr(hr, "get_active_chat_prompt", _slow_chat_prompt)

    t0 = time.monotonic()
    active, extra = asyncio.run(_chat_stream_setup_plain_english_panel(
        0.0, "A", 0, _fake_user(), "BASE", _fake_body(), "u1"))
    wall = time.monotonic() - t0

    assert wall < 0.62, f"plain-English panel took {wall:.2f}s — flag/rules ran sequentially"
    assert active is True
    i_chat = extra.index("=== ADMIN CHAT PROMPT")
    i_hr = extra.index("[HR]HR[/HR]")
    i_plain = extra.index(PLAIN_ENGLISH_EXPLAIN_CONTRACT)
    i_voice = extra.index(BUSINESS_OWNER_VOICE_CONTRACT)
    assert i_chat < i_hr < i_plain < i_voice, extra
    assert "BASE" in extra


def test_l1_plain_english_house_rules_failure_skips_chat_prompt(monkeypatch):
    """The house-rules pair keeps its shared-try semantics: a rules
    failure skips the chat-prompt read too (it must NOT be attempted),
    while the flag check and voice contract still apply."""
    ff = sys.modules["services.feature_flags"]
    hr = sys.modules["services.house_rules"]
    called = {}

    async def _flag_true(*a, **k):
        return True

    async def _failing_rules(*a, **k):
        raise RuntimeError("db down")

    async def _chat_prompt(*a, **k):
        called["chat_prompt"] = True
        return "CHAT"

    monkeypatch.setattr(ff, "is_enabled", _flag_true)
    monkeypatch.setattr(hr, "get_active_house_rules", _failing_rules)
    monkeypatch.setattr(hr, "get_active_chat_prompt", _chat_prompt)

    active, extra = asyncio.run(_chat_stream_setup_plain_english_panel(
        0.0, "A", 0, _fake_user(), "BASE", _fake_body(), "u1"))
    assert active is True
    assert "chat_prompt" not in called
    assert "=== ADMIN CHAT PROMPT" not in extra
    assert PLAIN_ENGLISH_EXPLAIN_CONTRACT in extra
    assert BUSINESS_OWNER_VOICE_CONTRACT in extra


# ── L2: true streaming + de-paced emission ───────────────────────────
def test_l2_emit_content_tokens_no_fixed_slicing_no_sleep():
    """6000 chars emit as twelve 512-char frames (11 x 512 + 368),
    joined back to the original, in < 0.5 s. The old loop needed
    1000 frames of 6 chars + 12 s of sleep for the same text."""
    content = "x" * 6000

    async def _collect():
        return [c async for c in _emit_content_tokens(content)]

    t0 = time.monotonic()
    frames = asyncio.run(_collect())
    wall = time.monotonic() - t0

    assert wall < 0.5, f"emission took {wall:.2f}s — pacing sleep is still there"
    assert len(frames) == 12, frames and [len(f) for f in frames]
    assert all(len(f) <= 512 for f in frames)
    assert all(len(f) > 6 for f in frames[:-1])
    assert "".join(frames) == content


def test_l2_emit_content_tokens_short_and_empty():
    assert asyncio.run(_collect_short()) == ["abc"]
    assert asyncio.run(_collect_empty()) == []


async def _collect_short():
    return [c async for c in _emit_content_tokens("abc")]


async def _collect_empty():
    return [c async for c in _emit_content_tokens("")]


def test_l2_replay_path_unchanged(monkeypatch):
    """The retry-replay path is deliberately UNTOUCHED: 16-char
    frames, 5 ms pacing. Pins the behaviour the task says to keep."""
    rec = {"content": "x" * 40, "provider": "ORA",
           "tool_calls_run": 0, "turn_state": "success"}
    body = SimpleNamespace(session_id="s1")

    async def _collect():
        frames = []
        async for raw in _replay_retry_gen(rec, body):
            frames.append(raw)
        return frames

    frames = asyncio.run(_collect())
    tokens = [json.loads(f[len("data: "):])["token"]
              for f in frames if '"token"' in f]
    assert tokens == ["x" * 16, "x" * 16, "x" * 8]
    assert any(json.loads(f[len("data: "):]).get("replayed_from_retry")
               for f in frames)
    src = inspect.getsource(_replay_retry_gen)
    assert "CHUNK = 16" in src and "asyncio.sleep(0.005)" in src


# ── L2: stream_openrouter_model ──────────────────────────────────────
def _sse_response(deltas, usage=None):
    """An SSE byte body: one `data:` line per delta, then [DONE]."""
    lines = []
    for d in deltas:
        lines.append(
            "data: " + json.dumps(
                {"choices": [{"delta": {"content": d}}]}) + "\n\n")
    if usage is not None:
        lines.append("data: " + json.dumps({"usage": usage}) + "\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _noop_ledger(monkeypatch):
    """The loop-token ledger is real in this tree; silence it so the
    streaming tests assert on transport behaviour, not ledger I/O."""
    import services.loop_token_ledger as _ltl

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(_ltl, "log_llm_usage", _noop)
    monkeypatch.setattr(_ltl, "log_real_usage", _noop)
    monkeypatch.setattr(_ltl, "current_ambient_user_id", lambda: None)


def _mock_openrouter_client(monkeypatch, handler):
    """Patch openrouter_client.get_shared_client to return ONE
    httpx client on a MockTransport, and count factory calls."""
    import httpx
    import services.llm.openrouter_client as _orc

    calls = {"factory": 0, "requests": 0, "returned": []}

    def _handler(request):
        calls["requests"] += 1
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    def _factory(timeout):
        calls["factory"] += 1
        calls["returned"].append(client)
        return client

    monkeypatch.setattr(_orc, "get_shared_client", _factory)
    return calls, client


def test_l2_stream_yields_provider_deltas(monkeypatch):
    """Deltas arrive exactly as the provider sent them — two yields,
    no fixed re-chunking, no sleep — with stream:true on the wire
    and exactly one request through the shared client."""
    import httpx
    import services.llm.openrouter_client as _orc

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _noop_ledger(monkeypatch)
    seen_payload = {}

    def _handler(request):
        seen_payload.update(json.loads(request.content))
        return httpx.Response(
            200, content=_sse_response(["Hello ", "world"]),
            headers={"Content-Type": "text/event-stream"})

    calls, client = _mock_openrouter_client(monkeypatch, _handler)

    async def _run():
        try:
            return [d async for d in _orc.stream_openrouter_model(
                "test-model", "sys", "hi")]
        finally:
            await client.aclose()

    assert asyncio.run(_run()) == ["Hello ", "world"]
    assert seen_payload["stream"] is True
    assert seen_payload["model"] == "test-model"
    assert calls["factory"] == 1 and calls["requests"] == 1


def test_l2_stream_falls_back_before_first_token(monkeypatch):
    """Primary 429 (no token yet) walks to the free chain — the same
    fallback order as the buffered caller — and serves from there."""
    import httpx
    import services.llm.openrouter_client as _orc

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _noop_ledger(monkeypatch)
    models = []

    def _handler(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "test-model":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200, content=_sse_response(["free ", "reply"]),
            headers={"Content-Type": "text/event-stream"})

    calls, client = _mock_openrouter_client(monkeypatch, _handler)

    async def _run():
        try:
            return [d async for d in _orc.stream_openrouter_model(
                "test-model", "sys", "hi")]
        finally:
            await client.aclose()

    assert asyncio.run(_run()) == ["free ", "reply"]
    assert models[0] == "test-model"
    assert models[1] == "openai/gpt-oss-120b:free"  # first free fallback
    assert calls["requests"] == 2


def test_l2_stream_non_retryable_aborts_chain(monkeypatch):
    """A 422 (genuinely malformed request) is not fallback-worthy: one
    request, then the error surfaces — no free-tier quota burned.
    NOTE (folded tree): a 400 no longer aborts here. Part XII (c2),
    folded into #375, made 400 fallback-worthy — a 400 from
    OpenRouter is often provider/model-side (routing, deprecation),
    not a broken prompt — so only 422 still aborts the chain
    (services/llm/openrouter_client.py _FALLBACK_STATUSES). This
    test was written against main, where 400 aborted; it now pins
    the 422 abort case instead."""
    import httpx
    import services.llm.openrouter_client as _orc

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _noop_ledger(monkeypatch)

    def _handler(request):
        return httpx.Response(422, json={"error": "unprocessable"})

    calls, client = _mock_openrouter_client(monkeypatch, _handler)

    async def _run():
        try:
            return [d async for d in _orc.stream_openrouter_model(
                "test-model", "sys", "hi")]
        finally:
            await client.aclose()

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_run())
    assert calls["requests"] == 1


def test_l2_stream_midstream_failure_raises_not_splices(monkeypatch):
    """Once the first token has flowed the model is committed: a
    mid-stream transport failure raises (never splices a fallback
    model's text onto the partial reply) and makes no second
    request."""
    import httpx
    import services.llm.openrouter_client as _orc

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _noop_ledger(monkeypatch)

    class _FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"part"}}]}\n\n'
            raise httpx.RemoteProtocolError("boom")

    def _handler(request):
        return httpx.Response(200, stream=_FailingStream(),
                              headers={"Content-Type": "text/event-stream"})

    calls, client = _mock_openrouter_client(monkeypatch, _handler)

    async def _run():
        got = []
        try:
            async for d in _orc.stream_openrouter_model(
                    "test-model", "sys", "hi"):
                got.append(d)
        finally:
            await client.aclose()
        return got

    with pytest.raises(httpx.RemoteProtocolError):
        asyncio.run(_run())
    assert calls["requests"] == 1


# ── L3: shared pooled clients ────────────────────────────────────────
def test_l3_shared_httpx_client_same_instance_per_timeout():
    """Same timeout → the SAME client object; a different timeout
    gets its own client (timeout semantics unchanged)."""
    import services.llm._http as _http

    async def _run():
        try:
            a = _http.get_shared_client(25.0)
            b = _http.get_shared_client(25.0)
            c = _http.get_shared_client(30.0)
            return a, b, c
        finally:
            await _http.aclose_shared_clients()

    a, b, c = asyncio.run(_run())
    assert a is b
    assert a is not c


def test_l3_two_buffered_calls_reuse_same_client(monkeypatch):
    """Two consecutive call_openrouter_model calls go through ONE
    shared client: both lookups return the same instance and the
    transport sees two requests. (Proves the per-call `async with
    AsyncClient` is gone, without changing the response shape.)"""
    import httpx
    import services.llm.openrouter_client as _orc

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    _noop_ledger(monkeypatch)
    calls, client = _mock_openrouter_client(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "hi"}}],
                       "usage": {}}))

    async def _run():
        try:
            r1 = await _orc.call_openrouter_model("test-model", "sys", "hi")
            r2 = await _orc.call_openrouter_model("test-model", "sys", "hi")
            return r1, r2
        finally:
            await client.aclose()

    assert asyncio.run(_run()) == ("hi", "hi")
    # The lookup runs once per call (expected) — what proves pooling
    # is that BOTH lookups returned the SAME client instance, which
    # then served both requests.
    assert calls["returned"][0] is calls["returned"][1]
    assert calls["requests"] == 2


def test_l3_deepseek_direct_reuses_shared_client(monkeypatch):
    """_call_deepseek_direct: two calls, one shared client."""
    import httpx
    import services.llm.openrouter_providers as _orp

    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk")
    _noop_ledger(monkeypatch)
    calls, client = _mock_openrouter_client(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "ds"}}],
                       "usage": {}}))
    # openrouter_providers imports get_shared_client at ITS module
    # top level, so patch it there (not on openrouter_client).
    def _counting_factory(timeout):
        calls["factory"] += 1
        calls["returned"].append(client)
        return client

    monkeypatch.setattr(_orp, "get_shared_client", _counting_factory)

    async def _run():
        try:
            r1 = await _orp._call_deepseek_direct(
                messages=[{"role": "user", "content": "hi"}], system="s")
            r2 = await _orp._call_deepseek_direct(
                messages=[{"role": "user", "content": "hi"}], system="s")
            return r1, r2
        finally:
            await client.aclose()

    assert asyncio.run(_run()) == ("ds", "ds")
    assert calls["returned"][0] is calls["returned"][1]
    assert calls["requests"] == 2


def test_l3_v2_llm_client_reuses_openai_client(monkeypatch):
    """Two stream_chat calls share ONE AsyncOpenAI client (both
    lookups return the same instance; create runs twice) and still
    stream provider deltas to done."""
    import services.llm._http as _http
    import services.ora_chat_v2.llm_client as _lc

    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.delenv("MOCK_LLM", raising=False)

    class _FakeStream:
        def __init__(self, texts):
            self._texts = texts

        async def __aiter__(self):
            for t in self._texts:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=t, tool_calls=None),
                        finish_reason=None)],
                    usage=None)

    class _FakeCompletions:
        def __init__(self):
            self.create_calls = []

        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            return _FakeStream(["v2 ", "hi"])

    class _FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    fake = _FakeOpenAI()
    factory_calls = []
    returned = []

    def _factory(**kwargs):
        factory_calls.append(kwargs)
        returned.append(fake)
        return fake

    monkeypatch.setattr(_http, "get_shared_openai_client", _factory)

    async def _run_once():
        events = []
        async for ev in _lc.stream_chat(
                messages=[{"role": "user", "content": "hi"}], db=None):
            events.append(ev)
        return events

    ev1 = asyncio.run(_run_once())
    ev2 = asyncio.run(_run_once())
    for ev in (ev1, ev2):
        assert [e for e in ev if e["type"] == "delta"] == [
            {"type": "delta", "content": "v2 "},
            {"type": "delta", "content": "hi"}]
        assert ev[-1] == {"type": "done"}
    assert len(factory_calls) == 2  # one lookup per call (expected)
    assert returned[0] is returned[1] is fake  # ...same instance both times
    assert factory_calls[0]["base_url"] == "https://example.invalid"
    assert factory_calls[0]["api_key"] == "k"
    assert len(fake.chat.completions.create_calls) == 2


def test_l3_groq_reuses_shared_client(monkeypatch):
    """Two _call_groq calls share ONE AsyncGroq client (constructed
    once, create called twice). The groq SDK is faked — it is not
    installed in this venv; the pooling seam is the factory."""
    import services.llm._http as _http
    import services.llm.groq_client as _gc

    created = []

    class _FakeAsyncGroq:
        def __init__(self, api_key, timeout):
            created.append(self)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="g"))],
                usage=None)

        async def aclose(self):
            pass

    fake_groq = types.ModuleType("groq")
    fake_groq.AsyncGroq = _FakeAsyncGroq
    monkeypatch.setitem(sys.modules, "groq", fake_groq)
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    _noop_ledger(monkeypatch)

    async def _run():
        try:
            r1 = await _gc._call_groq(
                messages=[{"role": "user", "content": "hi"}], system="s")
            r2 = await _gc._call_groq(
                messages=[{"role": "user", "content": "hi"}], system="s")
            return r1, r2
        finally:
            await _http.aclose_shared_clients()

    assert asyncio.run(_run()) == ("g", "g")
    assert len(created) == 1


def test_l3_loop_change_recreates_client():
    """A client cached on one event loop is dropped (not reused) on
    a different loop — the guard that keeps test harnesses (and any
    loop-recreating embedder) from reusing dead-loop connections."""
    import services.llm._http as _http

    async def _get():
        return _http.get_shared_client(25.0)

    c1 = asyncio.run(_get())
    c2 = asyncio.run(_get())
    assert c1 is not c2
    asyncio.run(_http.aclose_shared_clients())
    c3 = asyncio.run(_get())
    assert c3 is not c1 and c3 is not c2
    asyncio.run(_http.aclose_shared_clients())


def test_l3_aclose_empties_cache():
    """aclose_shared_clients() clears the cache: the next lookup
    builds a fresh client."""
    import services.llm._http as _http

    async def _run():
        a = _http.get_shared_client(25.0)
        await _http.aclose_shared_clients()
        b = _http.get_shared_client(25.0)
        await _http.aclose_shared_clients()
        return a, b

    a, b = asyncio.run(_run())
    assert a is not b
