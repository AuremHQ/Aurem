"""
P1k — mode-pill router wiring + judge-residual verify gate.

Proves, against the real modules (LLM judge stubbed only where
decide() would call it; Parliament LLM calls stubbed at the same seam
the findings tests use):

A. `services.mode_routing` — `normalize_mode_pill` /
   `resolve_loop_mode` resolve the user's pill through the EXISTING
   mode-resolution logic (a loop run is an agentic edit): swift + Pro
   access escalates to pro, maxx passes through, unknown/absent → None.
B. Pill → Parliament call count, using the exact decision expression
   `_gen_via_parliament` uses (`should_use_single_model(mode=<resolved
   pill>, task_type="code_fix", attempt=...)`): swift/pro attempt 1 →
   1 LLM call (single-model); maxx / no pill / retry attempt 2 → the
   full 3-member Council.
C. `routers/loop.py` — `StartBody` carries the pill; the start handler
   assigns `engine.mode` and persists it in the engine context (the
   dict `_doc()` persists whole, so it survives rehydration).
D. `lookup_or_rehydrate` restores `engine.mode` from the persisted
   context — the EXECUTE engine is the rehydrated instance, so without
   this the pill set at start would never reach `_do_execute`.
E. `CEO.decide` judge-residual verify gate — the judge's pick must
   pass `_syntax_valid` (the same check the scoring gate uses, reused
   not duplicated) before it is returned as the winner; if no
   candidate passes, manual_review.

NOTE on E: the findings pre-filter already drops a PROVABLY broken
`.py` candidate before the judge ever fires, so the gate's firing
shapes are the ones the pre-filter cannot condemn — no file context,
or an unverified extension (.ts). A `.py` candidate WITH file_path
never reaches the gate broken; the tests below pin that too.
"""
import asyncio
import importlib
import pathlib
import sys
import types

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.mode_routing import (  # noqa: E402
    MODE_PILLS,
    normalize_mode_pill,
    resolve_loop_mode,
)
from core.parliament.routing import should_use_single_model  # noqa: E402
from core.parliament.ceo import CEO  # noqa: E402

# Same snippets as the findings scoring tests.
BROKEN_PY = (
    "import os\nfrom pathlib import Path\n\n"
    "class BrokenThing:\n    def run(self):\n"
    "        return {\n            \"key\": \"value\",\n"
)
VALID_PY = BROKEN_PY + '            "other": "x",\n        }\n'
# Truncated TypeScript — unclosed brace, no parser here.
BROKEN_TS = (
    "export function greet(name: string): string {\n"
    "    return 'hi ' + name;\n"
)
VALID_TS = (
    "export function greet(name: string): string {\n"
    "    return 'hi ' + name;\n}\n"
)


def _votes(*items):
    return [
        {"member": m, "output": out, "score": s, "temp": t, "error": None}
        for m, out, s, t in items
    ]


# ─── A. Pill resolution (pure) ──────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("swift", "swift"),
    ("pro", "pro"),
    ("maxx", "maxx"),
    ("PRO", "pro"),
    ("  Maxx ", "maxx"),
    ("Swift", "swift"),
    (None, None),
    ("", None),
    ("   ", None),
    ("turbo", None),
    ("max", None),
    (123, None),
])
def test_normalize_mode_pill(raw, expected):
    assert normalize_mode_pill(raw) == expected


def test_mode_pills_vocabulary():
    assert set(MODE_PILLS) == {"swift", "pro", "maxx"}


@pytest.mark.parametrize("req_mode,account_has_pro,env,expected", [
    # Agentic edit on Swift with Pro access escalates to pro (the
    # existing resolve_model_mode rule for agentic turns).
    ("swift", True, "gated", "pro"),
    ("swift", True, "transparent", "pro"),
    ("SWIFT", True, "gated", "pro"),
    # No Pro access + gated mode: stays swift (caller shows the
    # upgrade offer instead — for loops the tiered gate already
    # blocks free/starter starts, so this arm is defence in depth).
    ("swift", False, "gated", "swift"),
    # ... unless transparent mode is on: then it still escalates.
    ("swift", False, "transparent", "pro"),
    ("pro", True, "gated", "pro"),
    ("pro", False, "gated", "pro"),
    ("maxx", True, "gated", "maxx"),
    ("maxx", False, "gated", "maxx"),
    (None, True, "gated", None),
    ("turbo", True, "gated", None),
    ("", True, "gated", None),
])
def test_resolve_loop_mode(req_mode, account_has_pro, env, expected, monkeypatch):
    monkeypatch.setenv("EDIT_TIER_MODE", env)
    assert resolve_loop_mode(req_mode, account_has_pro=account_has_pro) == expected


@pytest.mark.parametrize("pill,attempt,expected", [
    ("swift", 1, True),    # resolves to pro → single-model
    ("pro", 1, True),
    ("maxx", 1, False),    # maxx keeps the full Council
    (None, 1, False),      # unknown pill → full Council
    ("turbo", 1, False),   # garbage pill → full Council
    ("swift", 2, False),   # retry escalates to the full Council
    ("pro", 2, False),
])
def test_resolved_pill_drives_single_model_gate(pill, attempt, expected):
    mode = resolve_loop_mode(pill)
    assert should_use_single_model(
        mode=mode, task_type="code_fix", attempt=attempt) is expected


# ─── B. Pill → Parliament call count ────────────────────────────────
# The exact chain the engine runs per file: resolved mode →
# should_use_single_model → context single_model flag → Parliament.run.

def _parliament_with_stub(monkeypatch, content=VALID_PY):
    """Fresh Parliament with the protected LLM call stubbed. Returns
    (parliament, calls). Same seam as the findings single-model tests."""
    from core import parliament as parl_mod
    importlib.reload(parl_mod)
    calls = []

    async def fake_llm_call_protected(**kwargs):
        calls.append(kwargs)
        return (content, 10.0, None)

    monkeypatch.setattr(parl_mod.llm_call, "_llm_call_protected",
                        fake_llm_call_protected)
    return parl_mod.Parliament(db=None), calls


def _run_for_pill(parl, pill, *, attempt=1):
    """Run Parliament exactly the way _gen_via_parliament does for a
    code_fix file: resolve the pill, compute the flag, pass it in."""
    mode = resolve_loop_mode(pill)
    single = should_use_single_model(
        mode=mode, task_type="code_fix", attempt=attempt)
    ctx = {"task_type": "code_fix", "file_path": "x.py", "user_id": "u",
           "single_model": single}
    return asyncio.run(parl.run(task="fix x.py", context=ctx))


def test_swift_pill_takes_single_model_path(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run_for_pill(parl, "swift")
    assert decision["status"] == "success"
    assert decision["single_model"] is True
    assert len(calls) == 1, f"swift code_fix attempt 1 must make 1 call, got {len(calls)}"


def test_pro_pill_takes_single_model_path(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run_for_pill(parl, "pro")
    assert decision["status"] == "success"
    assert decision["single_model"] is True
    assert len(calls) == 1


def test_maxx_pill_takes_full_council(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run_for_pill(parl, "maxx")
    assert decision["status"] == "success"
    assert decision.get("single_model") is not True
    assert len(calls) == 3, f"maxx must run the full Council (3 calls), got {len(calls)}"


def test_no_pill_takes_full_council(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run_for_pill(parl, None)
    assert decision["status"] == "success"
    assert decision.get("single_model") is not True
    assert len(calls) == 3, "mode None still means full Council"


def test_swift_retry_attempt_takes_full_council(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run_for_pill(parl, "swift", attempt=2)
    assert decision["status"] == "success"
    assert decision.get("single_model") is not True
    assert len(calls) == 3, "retry (attempt 2) escalates to the full Council"


# ─── C. Router: StartBody carries the pill ─────────────────────────

class _StubbedLoopRouter:
    """Context manager: import routers.loop with its unavailable
    heavy deps stubbed (cto_services, feature flags, rate limiter,
    the LoopEngine module itself — all imported at module top, all
    used only inside endpoint functions). Restores sys.modules on
    exit so no other test file sees the stubs."""

    def __init__(self):
        self.saved = {}
        self.added = []

    def _stub(self, name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        if name in sys.modules:
            self.saved[name] = sys.modules[name]
        else:
            self.added.append(name)
        sys.modules[name] = mod
        return mod

    def __enter__(self):
        async def _current_dev(authorization=None):
            return {"user_id": "u1", "tier": "pro"}

        self._stub("cto_services")
        self._stub("cto_services.auth", current_dev=_current_dev)
        self._stub("cto_services.db", get_db=lambda: None)
        self._stub("services.feature_flags", is_enabled=lambda *a, **k: False)
        self._stub("services.rate_limiter",
                   check_rate_limit_async=lambda *a, **k: None)
        eng = self._stub("services.loop_engine")
        eng.new_loop_id = lambda: "loop_test"
        eng.register = lambda e: None
        eng.lookup_or_rehydrate = None
        import routers.loop as loop_mod
        return loop_mod

    def __exit__(self, *exc):
        for name in self.added:
            sys.modules.pop(name, None)
        sys.modules.update(self.saved)
        for name in ("routers.loop", "routers"):
            sys.modules.pop(name, None)
        return False


def test_start_body_carries_mode_pill():
    with _StubbedLoopRouter() as loop_mod:
        body = loop_mod.StartBody(user_message="fix it", mode="swift")
        assert body.mode == "swift"
        assert loop_mod.StartBody(user_message="fix it").mode is None
        assert loop_mod.StartBody(
            user_message="fix it", mode="maxx").mode == "maxx"


def test_start_body_passes_unknown_pill_through_raw():
    """CONFLICT 1 resolution (Teji's decision, folded tree): the
    request model still does NOT validate the vocabulary — an unknown
    pill passes through StartBody raw, and resolve_loop_mode still
    normalizes it to None (full Council) at the resolver level.
    BUT part13's start_loop now rejects unknown pills with 422
    `invalid_loop_mode` BEFORE the resolver runs, so the resolver's
    unknown→None arm is never reached from the router — it is
    defence in depth, not the live path. The old version of this
    test asserted the start must NOT 422; that is no longer true."""
    with _StubbedLoopRouter() as loop_mod:
        body = loop_mod.StartBody(user_message="fix it", mode="turbo")
        assert body.mode == "turbo"
        assert resolve_loop_mode(body.mode) is None
        src = (BACKEND / "routers" / "loop.py").read_text()
        assert '"invalid_loop_mode"' in src
        # The 422 validation precedes the resolver call in start_loop.
        assert src.index("invalid_loop_mode") < src.index(
            "resolve_loop_mode(body.mode")


def test_router_assigns_engine_mode_and_persists_it():
    """Source guard — the engine itself is too heavy to instantiate
    here (repo + Mongo + full router surface), so the two assignment
    lines in start_loop are pinned by source, next to the engine
    construction the findings lane's comment describes."""
    src = (BACKEND / "routers" / "loop.py").read_text()
    assert "mode:         Optional[str] = Field(None, max_length=16)" in src
    assert ("engine.mode = resolve_loop_mode(body.mode, "
            "account_has_pro=_account_has_pro)") in src
    assert 'engine.context["mode"] = engine.mode' in src
    import py_compile
    py_compile.compile(str(BACKEND / "routers" / "loop.py"), doraise=True)


# ─── D. Rehydration restores the pill ───────────────────────────────

def test_rehydration_restore_mapping():
    """The restore expression in lookup_or_rehydrate reads the pill
    out of the persisted context dict and normalizes it — the same
    values the router wrote at start must round-trip, and anything
    else must fall back to None (full Council)."""
    cases = [
        ({"mode": "swift"}, "swift"),
        ({"mode": "PRO"}, "pro"),
        ({"mode": "maxx"}, "maxx"),
        ({"mode": None}, None),
        ({"mode": "turbo"}, None),
        ({}, None),
    ]
    for context, expected in cases:
        assert normalize_mode_pill((context or {}).get("mode")) == expected


def test_rehydration_restores_engine_mode_from_context():
    """Source guard — lookup_or_rehydrate rebuilds a fresh engine for
    confirm (a different request/worker than start), so it must
    restore engine.mode from the persisted context, or the pill set
    at start would never reach _do_execute."""
    src = (BACKEND / "services" / "loop_engine.py").read_text()
    fn = src.split("async def lookup_or_rehydrate")[1].split(
        "# Defensive shutdown hook")[0]
    assert 'eng.mode = _norm_pill((doc.get("context") or {}).get("mode"))' in fn
    assert "from services.mode_routing import normalize_mode_pill" in fn
    import py_compile
    py_compile.compile(str(BACKEND / "services" / "loop_engine.py"),
                       doraise=True)


# ─── E. CEO judge-residual verify gate ──────────────────────────────

def _judge_pick(idx, calls):
    async def _fake(*a, **k):
        calls.append(k.get("candidates"))
        return idx
    return _fake


def test_judge_pick_with_broken_syntax_rejected_no_file_path():
    """The task's test: the judge picks the broken candidate even
    though it has the highest score — it must NOT be returned as the
    winner. No file_path, so the findings pre-filter cannot condemn
    it (tri-state) and the judge fires; the verify gate rejects the
    pick and, with no passing candidate, returns manual_review."""
    ceo = CEO()
    judge_calls = []
    ceo._llm_judge = _judge_pick(0, judge_calls)
    decision = asyncio.run(ceo.decide(
        task="fix it",
        votes=_votes(("A1", BROKEN_PY, 0.40, 0.1)),
        context={"task_type": "code_fix", "council": "A"},
    ))
    assert len(judge_calls) == 1, "judge must have fired (pre-filter cannot drop this)"
    assert decision["status"] == "manual_review"
    assert decision["output"] is None
    assert decision["winner"] is None
    assert "verify gate" in decision["reasoning"]


def test_judge_pick_broken_typescript_rejected():
    """Production-realistic shape: a .ts file. The scorer caps it
    below the floor (unverified), the judge picks it from excerpts,
    the gate rejects it — no parser here can pass it, so the file
    goes to manual_review instead of shipping an unverified guess."""
    ceo = CEO()
    judge_calls = []
    ceo._llm_judge = _judge_pick(0, judge_calls)
    decision = asyncio.run(ceo.decide(
        task="fix greet.ts",
        votes=_votes(("A1", BROKEN_TS, 0.30, 0.1)),
        context={"task_type": "code_fix", "file_path": "greet.ts",
                 "council": "A"},
    ))
    assert len(judge_calls) == 1
    assert decision["status"] == "manual_review"
    assert decision["output"] is None
    assert "verify gate" in decision["reasoning"]


def test_judge_pick_broken_falls_back_to_valid_candidate():
    """Broken pick (highest score, 0.40) + valid candidate (0.30):
    the broken pick is rejected and the best candidate that PASSES
    the gate wins instead of failing the whole file."""
    ceo = CEO()
    ceo._llm_judge = _judge_pick(0, [])
    decision = asyncio.run(ceo.decide(
        task="fix it",
        votes=_votes(("A1", BROKEN_PY, 0.40, 0.1),
                     ("A2", VALID_PY, 0.30, 0.2)),
        context={"task_type": "code_fix", "council": "A"},
    ))
    assert decision["status"] == "success"
    assert decision["winner"] == "A2"
    assert decision["output"] == VALID_PY
    assert decision["ceo_picked"] is False
    assert "verify gate" in decision["reasoning"]


def test_valid_typescript_judge_pick_still_manual_review():
    """Fail-closed, pinned deliberately: even a VALID .ts candidate
    cannot pass _syntax_valid (no TS parser here), so a below-floor
    TS pool has no passing candidate and goes to manual_review.
    A TS checker is the follow-up; shipping the judge's unverified
    guess is what this gate exists to stop."""
    ceo = CEO()
    ceo._llm_judge = _judge_pick(0, [])
    decision = asyncio.run(ceo.decide(
        task="fix greet.ts",
        votes=_votes(("A1", VALID_TS, 0.30, 0.1)),
        context={"task_type": "code_fix", "file_path": "greet.ts",
                 "council": "A"},
    ))
    assert decision["status"] == "manual_review"
    assert decision["output"] is None
    assert "verify gate" in decision["reasoning"]


def test_judge_pick_valid_python_returned_unchanged():
    """Positive control: a valid Python pick below the floor passes
    the gate and is returned exactly as before (ceo_picked True)."""
    ceo = CEO()
    ceo._llm_judge = _judge_pick(0, [])
    decision = asyncio.run(ceo.decide(
        task="fix it",
        votes=_votes(("A1", VALID_PY, 0.30, 0.1)),
        context={"task_type": "code_fix", "council": "A"},
    ))
    assert decision["status"] == "success"
    assert decision["winner"] == "A1"
    assert decision["output"] == VALID_PY
    assert decision["ceo_picked"] is True
    assert "verify gate" not in decision["reasoning"]


def test_judge_gate_skipped_for_non_code_tasks():
    """The gate is a code concept: an analysis pick is returned
    untouched even though the prose would never pass a syntax check."""
    ceo = CEO()
    ceo._llm_judge = _judge_pick(0, [])
    prose = ("Analysis: revenue fell 12% in Q3 across 4 regions. "
             "We recommend cutting spend and suggest a review next week.")
    decision = asyncio.run(ceo.decide(
        task="analyze revenue",
        votes=_votes(("B1", prose, 0.30, 0.3)),
        context={"task_type": "analysis", "council": "B"},
    ))
    assert decision["status"] == "success"
    assert decision["winner"] == "B1"
    assert decision["output"] == prose


def test_judge_returns_none_still_manual_review():
    """Judge cannot break the tie → manual_review, unchanged."""
    ceo = CEO()

    async def _no_judge(*a, **k):
        return None

    ceo._llm_judge = _no_judge
    decision = asyncio.run(ceo.decide(
        task="fix it",
        votes=_votes(("A1", VALID_PY, 0.30, 0.1)),
        context={"task_type": "code_fix", "file_path": "x.py",
                 "council": "A"},
    ))
    assert decision["status"] == "manual_review"
    assert decision["output"] is None


def test_above_floor_winner_never_reaches_gate():
    """Regression guard: the gate lives only in the below-floor judge
    branch — a valid above-floor winner still auto-passes and the
    judge is never consulted."""
    ceo = CEO()

    async def _never_judge(*a, **k):
        raise AssertionError("judge must not fire above the floor")

    ceo._llm_judge = _never_judge
    decision = asyncio.run(ceo.decide(
        task="fix fixed.py",
        votes=_votes(("A1", VALID_PY, 0.92, 0.1)),
        context={"task_type": "code_fix", "file_path": "fixed.py",
                 "council": "A"},
    ))
    assert decision["status"] == "success"
    assert decision["output"] == VALID_PY


def test_broken_py_with_file_path_still_dropped_by_prefilter():
    """Pins the division of labour: a broken .py candidate WITH
    file_path is dropped by the findings pre-filter before the judge
    fires, so the gate never sees it — the judge is not consulted."""
    ceo = CEO()
    judge_calls = []

    async def _never_judge(*a, **k):
        judge_calls.append(1)
        return None

    ceo._llm_judge = _never_judge
    decision = asyncio.run(ceo.decide(
        task="fix broken.py",
        votes=_votes(("A1", BROKEN_PY, 0.40, 0.1)),
        context={"task_type": "code_fix", "file_path": "broken.py",
                 "council": "A"},
    ))
    assert decision["status"] == "manual_review"
    assert "syntax gate" in decision["reasoning"]
    assert judge_calls == []
