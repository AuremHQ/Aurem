"""Part XII group 4 — routers / adapters / session / evals (part12b).

Covers verify-part12-routers-adapters.md:
  a1  router current\\b -> \\bcurrent\\b (concurrent no longer routes)
  a2  "don't commit" must NOT classify as INTENT_CODE_CHANGE
  b1  ambiguity contraction bypass ("don't fix it" == "fix it")
  b2  omitted session_id must not share a global "" bucket
  b3  yes/ok -> NO_PENDING_PROPOSAL_MESSAGE is BY DESIGN (no change;
      characterization test only)
  c1  ContextVar default must not be a shared mutable dict
  c2  HTTP 400 in call_openrouter_model must fall through to the
      free / DeepSeek / Groq chain
  c3  tool_executor must unwrap inner ok: False by default
  d1-boundary  session.py must not drop boundary messages
  d1-slice     $slice: -400 was WRONG-or-STALE — nothing to build
  d2  quality_monitor must use user_msg (banana bread != 1.0)
  d3-scope  evals runner must dispatch scope_scorer
  d3-keys   runner must NOT return ok: True with no API keys;
      main() must exit non-zero

Negative control: PART12B_BACKEND=<pristine backend dir> pytest <file>
"""
import asyncio
import contextvars
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

BACKEND = Path(os.environ.get(
    "PART12B_BACKEND", Path(__file__).resolve().parents[1]))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _stub(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def _stub_pkg(name, path=None):
    if name in sys.modules:
        return sys.modules[name]
    m = types.ModuleType(name)
    m.__path__ = [str(path)] if path else []
    sys.modules[name] = m
    return m


# Stubs are installed at MODULE IMPORT time, not in a fixture: several
# fixtures in this file are module-scoped and are pulled in by a
# function-scoped autouse fixture, and pytest instantiates those before
# a module-scoped autouse fixture would run — so a fixture-installed
# stub arrives too late (ModuleNotFoundError on first setup).
_stub_pkg("cto_services")
_stub("cto_services.db", get_db=lambda: None, set_db=lambda db: None)
_stub("services.chat_helpers",
      classify_intent=lambda msg, payload, project: "A")
_stub("services.loop_token_ledger",
      current_ambient_user_id=lambda: None,
      log_llm_usage=AsyncMock(return_value=None),
      log_real_usage=AsyncMock(return_value=None))
# services.llm as a stub PACKAGE (real __init__ is heavy and pulls
# emergentintegrations): __path__ still resolves the real _state.py /
# openrouter_client.py files underneath it.
_stub_pkg("services.llm", BACKEND / "services" / "llm")
# services.repo_context is NOT in this lane's seeded tree (the evals
# runner imports it lazily inside _project_scoping_isolation_test,
# before its db-is-None early return). The stub is never called in
# these tests — get_db() returns None first.
_stub("services.repo_context",
      get_repo_context=AsyncMock(return_value=""))


# ─────────────────────── (a1) router ─────────────────────────────
@pytest.fixture(scope="module")
def router():
    import services.ora_chat.router as m
    return m


class TestCurrentWordBoundary:
    def test_concurrent_does_not_route_to_research(self, router):
        assert router.classify_intent(
            "fix concurrent map access in Go — data race on sync.Map") == "general"

    def test_concurrent_programming_is_general(self, router):
        assert router.classify_intent(
            "explain concurrent programming in Python") == "general"

    def test_standalone_current_still_research(self, router):
        assert router.classify_intent(
            "what is the current status of the build") == "research"

    def test_latest_still_research(self, router):
        assert router.classify_intent("what is the latest news") == "research"

    def test_plain_coding_is_general(self, router):
        assert router.classify_intent("fix the login bug") == "general"


# ─────────────────── (a2) intent_router ──────────────────────────
@pytest.fixture(scope="module")
def intent_router():
    import services.ora_chat.intent_router as m
    return m


class TestNegationWins:
    def test_dont_commit_is_preview(self, intent_router):
        intent, _ = intent_router.classify_intent_regex("don't commit")
        assert intent == intent_router.INTENT_PREVIEW

    def test_dont_commit_with_preview_phrase(self, intent_router):
        intent, _ = intent_router.classify_intent_regex(
            "don't commit this, just preview it")
        assert intent == intent_router.INTENT_PREVIEW

    def test_do_not_push_is_preview(self, intent_router):
        intent, _ = intent_router.classify_intent_regex("do not push")
        assert intent == intent_router.INTENT_PREVIEW

    def test_show_sample_dont_commit_is_preview(self, intent_router):
        intent, _ = intent_router.classify_intent_regex(
            "show me a sample, don't commit")
        assert intent == intent_router.INTENT_PREVIEW

    def test_plain_commit_is_code_change(self, intent_router):
        intent, _ = intent_router.classify_intent_regex("commit this fix")
        assert intent == intent_router.INTENT_CODE_CHANGE

    def test_negated_one_verb_other_verb_changes(self, intent_router):
        intent, _ = intent_router.classify_intent_regex(
            "don't commit, but do deploy it")
        assert intent == intent_router.INTENT_CODE_CHANGE


# ─────────────────── (b1) ambiguity_gate ─────────────────────────
@pytest.fixture(scope="module")
def ambiguity():
    import services.ambiguity_gate as m
    return m


class TestContractionBypass:
    def test_dont_fix_it_is_ambiguous(self, ambiguity):
        assert ambiguity.is_ambiguous_task("don't fix it") is True

    def test_fix_it_is_ambiguous(self, ambiguity):
        assert ambiguity.is_ambiguous_task("fix it") is True

    def test_its_broken_is_ambiguous(self, ambiguity):
        assert ambiguity.is_ambiguous_task("it's broken") is True

    def test_lone_dont_is_ambiguous(self, ambiguity):
        assert ambiguity.is_ambiguous_task("don't") is True

    def test_quoted_file_is_not_ambiguous(self, ambiguity):
        assert ambiguity.is_ambiguous_task("fix 'Signup.jsx'") is False

    def test_double_quoted_is_not_ambiguous(self, ambiguity):
        assert ambiguity.is_ambiguous_task('fix "the thing"') is False

    def test_specific_task_is_not_ambiguous(self, ambiguity):
        assert ambiguity.is_ambiguous_task(
            "fix the signup form validation in Signup.jsx") is False


# ─────────────── (b2) semantic_mode_router ───────────────────────
@pytest.fixture(scope="module")
def smr():
    import services.semantic_mode_router as m
    return m


@pytest.fixture(autouse=True)
def _clean_session_state(smr):
    smr._SESSION_STATE.clear()
    yield
    smr._SESSION_STATE.clear()


@pytest.mark.asyncio
async def test_no_session_id_shares_no_bucket(smr, monkeypatch):
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100")
    r1 = await smr.classify_mode_semantic("fix the login bug", None, None, None)
    r2 = await smr.classify_mode_semantic("audit the repo", None, None, "")
    assert r1["mode"] == "A" and r2["mode"] == "A"
    assert "" not in smr._SESSION_STATE
    assert smr._SESSION_STATE == {}


@pytest.mark.asyncio
async def test_real_session_id_still_gets_state(smr, monkeypatch):
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100")
    with patch.object(smr, "_ask_jev_for_mode",
                      new=AsyncMock(return_value={"mode": "D",
                                                  "confidence": 0.95})):
        r = await smr.classify_mode_semantic("audit the repo", None, None, "s1")
    assert r["mode"] == "D"
    assert "s1" in smr._SESSION_STATE


# ─────────────── (b3) intent_grounding — BY DESIGN ───────────────
@pytest.fixture(scope="module")
def grounding():
    import services.intent_grounding as m
    return m


class TestNoPendingByDesign:
    """b3 is BY DESIGN on /loop/start only — no code change. These
    characterization tests pin the current behaviour so a future
    "fix" attempt can see it is intentional."""

    @pytest.mark.asyncio
    async def test_cold_yes_no_session_is_no_pending(self, grounding):
        out = await grounding.resolve_confirmatory_scope(
            None, "u", None, "yes")
        assert out == {"grounded": True, "no_pending": True}

    @pytest.mark.asyncio
    async def test_cold_ok_no_session_is_no_pending(self, grounding):
        out = await grounding.resolve_confirmatory_scope(
            None, "u", None, "ok")
        assert out == {"grounded": True, "no_pending": True}

    @pytest.mark.asyncio
    async def test_non_confirmatory_is_ungrounded(self, grounding):
        out = await grounding.resolve_confirmatory_scope(
            None, "u", None, "fix the login bug")
        assert out == {"grounded": False}


# ─────────────────── (c1) llm/_state ────────────────────────────
@pytest.fixture(scope="module")
def llm_state():
    import services.llm._state as m
    return m


class TestProvenanceIsolation:
    def test_fresh_contexts_do_not_share_default(self, llm_state):
        ctx_a = contextvars.Context()
        ctx_b = contextvars.Context()
        ctx_a.run(llm_state._set_last_provider, "groq",
                  "llama-3.3-70b-versatile")
        assert ctx_a.run(llm_state.get_last_provider)["provider"] == "groq"
        # B never set anything: it must see the default, not A's Groq.
        assert ctx_b.run(llm_state.get_last_provider)["provider"] == "openrouter"
        assert ctx_b.run(llm_state.get_last_provider)["is_emergency"] is False

    def test_get_returns_fresh_dict_each_time(self, llm_state):
        ctx = contextvars.Context()
        first = ctx.run(llm_state.get_last_provider)
        first["provider"] = "tampered"
        assert ctx.run(llm_state.get_last_provider)["provider"] == "openrouter"

    @pytest.mark.asyncio
    async def test_parent_child_sharing_still_works(self, llm_state):
        # The intended mechanism: reset installs a dict in the
        # request context; a child task mutating it IS visible to
        # the parent (same dict object by reference).
        llm_state.reset_last_provider()

        async def child():
            llm_state._set_last_provider("groq", "llama-3.3-70b-versatile")

        await asyncio.create_task(child())
        got = llm_state.get_last_provider()
        assert got["provider"] == "groq"
        assert got["is_emergency"] is True
        llm_state.reset_last_provider()


# ───────────────── (c2) openrouter 400 ───────────────────────────
@pytest.fixture(scope="module")
def orc():
    llm_pkg = sys.modules["services.llm"]
    llm_pkg._openrouter_key = lambda: "test-or-key"
    llm_pkg._deepseek_direct_key = lambda: "test-ds-key"
    llm_pkg._groq_key = lambda: None
    llm_pkg._DEEPSEEK_DIRECT_MODEL = "deepseek-chat"
    llm_pkg._GROQ_MODEL = "groq-model"
    llm_pkg._call_groq = AsyncMock(return_value="")
    import services.llm.openrouter_client as m
    return m


def _resp(status, content=""):
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    body = {"choices": [{"message": {"content": content}}]} if status == 200 else {}
    return httpx.Response(status, json=body, request=req)


class _FakeClient:
    """httpx.AsyncClient stand-in: per-model scripted responses."""
    def __init__(self, script):
        self.script = script  # model -> (status, content) or Exception
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        model = (json or {}).get("model")
        self.posts.append(model)
        entry = self.script.get(model, (500, ""))
        if isinstance(entry, Exception):
            raise entry
        status, content = entry
        return _resp(status, content)


def _http_error(status):
    req = httpx.Request("POST", "https://x")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"{status}", request=req, response=resp)


class TestOpenRouter400:
    def test_400_is_fallback_worthy(self, orc):
        assert orc._is_fallback_worthy(_http_error(400)) is True

    def test_422_still_aborts(self, orc):
        assert orc._is_fallback_worthy(_http_error(422)) is False

    def test_429_still_fallback_worthy(self, orc):
        assert orc._is_fallback_worthy(_http_error(429)) is True

    @pytest.mark.asyncio
    async def test_primary_400_falls_through_to_free(self, orc, monkeypatch):
        monkeypatch.setenv("OPENROUTER_FREE_MODELS", "free-a,free-b")
        sys.modules["services.llm"]._call_deepseek_direct = AsyncMock(
            return_value="")
        client = _FakeClient({
            "paid-model": (400, ""),
            "free-a": (200, "free-model answer"),
        })
        with patch.object(httpx, "AsyncClient", return_value=client):
            out = await orc.call_openrouter_model(
                "paid-model", "sys", "hello")
        assert out == "free-model answer"
        assert client.posts[0] == "paid-model"
        assert "free-a" in client.posts

    @pytest.mark.asyncio
    async def test_deepseek_400_falls_through_to_free(self, orc, monkeypatch):
        monkeypatch.setenv("OPENROUTER_FREE_MODELS", "free-a")
        llm_pkg = sys.modules["services.llm"]
        llm_pkg._call_deepseek_direct = AsyncMock(
            side_effect=_http_error(400))
        client = _FakeClient({
            "paid-model": (400, ""),
            "free-a": (200, "free-model answer"),
        })
        with patch.object(httpx, "AsyncClient", return_value=client):
            out = await orc.call_openrouter_model(
                "paid-model", "sys", "hello")
        assert out == "free-model answer"

    @pytest.mark.asyncio
    async def test_primary_422_returns_empty_without_free(self, orc, monkeypatch):
        monkeypatch.setenv("OPENROUTER_FREE_MODELS", "free-a")
        client = _FakeClient({"paid-model": (422, "")})
        with patch.object(httpx, "AsyncClient", return_value=client):
            out = await orc.call_openrouter_model(
                "paid-model", "sys", "hello")
        assert out == ""
        assert client.posts == ["paid-model"]


# ─────────────────── (c3) tool_executor ──────────────────────────
@pytest.fixture(scope="module")
def tex():
    import services.tool_executor as m
    return m


class TestToolExecutorUnwrap:
    @pytest.mark.asyncio
    async def test_inner_ok_false_is_outer_ok_false_by_default(
            self, tex, monkeypatch):
        monkeypatch.delenv("INTERACTION_CONTRACT_V1", raising=False)

        async def failing_runner():
            return {"ok": False, "error": "boom"}

        out = await tex.execute("read_repo_file", failing_runner)
        assert out["ok"] is False
        assert out["data"]["ok"] is False

    @pytest.mark.asyncio
    async def test_collect_signals_sees_the_failure(self, tex, monkeypatch):
        monkeypatch.delenv("INTERACTION_CONTRACT_V1", raising=False)

        async def failing_runner():
            return {"ok": False, "error": "boom"}

        out = await tex.execute("read_repo_file", failing_runner)
        assert len(tex.collect_signals([out])) == 1

    @pytest.mark.asyncio
    async def test_success_still_ok_true(self, tex):
        async def ok_runner():
            return {"ok": True, "content": "x"}

        out = await tex.execute("read_repo_file", ok_runner)
        assert out["ok"] is True
        assert out["data"] == {"ok": True, "content": "x"}

    @pytest.mark.asyncio
    async def test_exception_path_unchanged(self, tex):
        async def raising_runner():
            raise ValueError("HTTP 401 Bad credentials")

        out = await tex.execute("read_repo_file", raising_runner)
        assert out["ok"] is False
        assert out["system_signal"] == "github_auth_failed"


# ───────────────── (d1-boundary) session ─────────────────────────
@pytest.fixture()
def sess(monkeypatch):
    import services.ora_chat.session as m
    monkeypatch.setattr(m, "CONTEXT_TOKEN_CEILING", 100)
    monkeypatch.setattr(m, "TAIL_TOKEN_BUDGET", 50)
    return m


def _msg(role, letter):
    # 116 chars -> int(116 / 3.5) + 1 == 34 tokens each.
    return {"role": role, "content": f"MSG-{letter} " + "y" * 110}


@pytest.mark.asyncio
async def test_boundary_message_is_not_dropped(sess):
    msgs = [_msg("user", "A"), _msg("assistant", "B"),
            _msg("user", "C"), _msg("assistant", "D")]
    session = {"messages": msgs, "rolling_summary": "OLD SUMMARY"}
    out = await sess.build_llm_messages(session)
    contents = [m["content"] for m in out]
    assert any("MSG-D" in c for c in contents), "tail must hold D"
    # C is the boundary message: it must be EITHER in the tail OR
    # covered by the fold (summary_up_to == 3, tested below) —
    # never in neither. The tail here is [D] only, so the fold must
    # cover C; assert the shared helper agrees.
    assert sess._tail_start_index(
        sess._messages_to_llm_format(msgs)) == 3


@pytest.mark.asyncio
async def test_fold_covers_boundary_message(sess):
    seen = {}

    async def fake_one_shot(**kw):
        seen["messages"] = kw.get("messages") or []
        return ("NEW SUMMARY", {}, None)

    class FakeColl:
        def __init__(self):
            self.doc = {"messages": [
                _msg("user", "A"), _msg("assistant", "B"),
                _msg("user", "C"), _msg("assistant", "D")],
                "rolling_summary": "", "summary_up_to": 0}
            self.sets = None

        async def find_one(self, *a, **kw):
            return self.doc

        async def update_one(self, filt, update, upsert=False):
            self.sets = update.get("$set", {})

    class FakeDB:
        def __init__(self):
            self.coll = FakeColl()
            self.ora_chat_sessions = self.coll

        def __getitem__(self, name):
            return self.coll

    db = FakeDB()
    providers_stub = types.ModuleType("services.ora_chat.providers")
    providers_stub.one_shot = fake_one_shot
    sys.modules["services.ora_chat.providers"] = providers_stub
    import services.ora_chat.router as _router
    with patch.object(sess, "get_db", return_value=db), \
         patch.object(_router, "resolve",
                      return_value={"top_p": 0.9, "presence_penalty": 0.0}):
        await sess.maybe_update_summary("s1", "u1")
    prompt = seen["messages"][0]["content"]
    assert "MSG-A" in prompt and "MSG-B" in prompt and "MSG-C" in prompt
    assert "MSG-D" not in prompt, "D is the tail, not the fold"
    assert db.coll.sets["summary_up_to"] == 3


# ─────────────────── (d2) quality_monitor ────────────────────────
@pytest.fixture(scope="module")
def qm():
    import core.quality_monitor as m
    return m


BANANA_REPLY = (
    "To make banana bread, first preheat your oven and mash three ripe "
    "bananas in a large bowl until smooth. Stir in melted butter, sugar, "
    "eggs, and vanilla, then fold in flour, baking soda, and a pinch of "
    "salt. Pour the batter into a greased loaf pan and bake until golden "
    "and a toothpick comes out clean. Let it cool before slicing and enjoy "
    "with butter for breakfast or an afternoon snack with tea."
)
ON_TOPIC_REPLY = (
    "Fix the signup form validation in Signup.jsx by checking the "
    "onSubmit handler, the required-field rules, and the error message "
    "display for empty fields."
)


class TestTopicalScoring:
    def test_banana_bread_does_not_score_1(self, qm):
        mon = qm.QualityMonitor()
        out = mon._compute_score(
            BANANA_REPLY,
            "How do I fix the signup form validation in Signup.jsx?",
            "agentic")
        assert out["score"] <= 0.6
        assert "off_topic_no_overlap" in out["flags"]
        assert out["topical_overlap"] == 0

    def test_on_topic_reply_stays_high(self, qm):
        mon = qm.QualityMonitor()
        out = mon._compute_score(
            ON_TOPIC_REPLY,
            "How do I fix the signup form validation in Signup.jsx?",
            "agentic")
        assert out["score"] >= 0.9

    def test_short_casual_exchange_not_penalised(self, qm):
        mon = qm.QualityMonitor()
        out = mon._compute_score("Hi there! How can I help?", "hello", "casual")
        assert out["score"] == 1.0
        assert out["topical_overlap"] is None


# ─────────────────── (d3) evals runner ───────────────────────────
@pytest.fixture(scope="module")
def runner_mod():
    import evals.runner as m
    return m


class TestScopeDispatcher:
    def test_scope_bleed_is_dispatched(self, runner_mod):
        spec = {"scorers": [("scope", {"intended_repo": "a->p",
                                       "visible_repo": "b->q"})]}
        out = runner_mod._dispatch_scorers(spec, {"content": ""})
        assert len(out) == 1
        assert out[0].scorer == "scope"
        assert out[0].status == "FAIL"

    def test_scope_intact_passes(self, runner_mod):
        spec = {"scorers": [("scope", {"intended_repo": "a->p",
                                       "visible_repo": None})]}
        out = runner_mod._dispatch_scorers(spec, {"content": ""})
        assert out[0].status == "PASS"

    def test_unknown_scorer_still_partial(self, runner_mod):
        spec = {"scorers": [("nope", {})]}
        out = runner_mod._dispatch_scorers(spec, {"content": ""})
        assert out[0].status == "PARTIAL"


class TestNoKeysFail:
    @pytest.mark.asyncio
    async def test_run_without_keys_is_not_ok(self, runner_mod, monkeypatch):
        monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("SEMANTIC_MODE_ROUTER_ENABLED", raising=False)
        report = await runner_mod.run(quick=False)
        assert report["ok"] is False
        assert "no LLM key" in report["skipped"]
        # Offline tests still ran (not skipped silently).
        assert report["routing_regression"] is not None
        assert report["input_normalizer"] is not None
        assert report["project_scoping"] is not None
        assert report["summary"]["blocked"] is True

    def test_main_exits_nonzero_without_keys(
            self, runner_mod, monkeypatch, tmp_path):
        monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("SEMANTIC_MODE_ROUTER_ENABLED", raising=False)
        monkeypatch.setattr(sys, "argv", ["runner", "--quick", "--report",
                                          str(tmp_path / "r.json")])
        assert runner_mod.main() == 1
