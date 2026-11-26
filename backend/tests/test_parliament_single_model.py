"""
Findings fix 2026-11-25 — Parliament single-model shortcut (swift/pro).

Proves:
  • Parliament.run with context single_model=True + task_type code_fix
    makes exactly ONE protected LLM call (not a 3-member vote), returns
    success with the single-model label, and threads the dynamic
    max_tokens_override (capped at 32_000) into that call.
  • Without the flag (maxx / retry / default) the full 3-member Council
    still runs — Parliament.run itself is unchanged for those paths.
  • The flag is ignored for non-code tasks, so TaskRouter B/C selection
    is preserved.
  • should_use_single_model(): swift/pro + code_fix + attempt 1 only;
    maxx, unknown mode, and retry attempts keep the full Council.
  • LoopEngine threading (source guards — the engine itself is too heavy
    to instantiate here): `mode` field, attempt passed through the
    bounded retry, single_model flag in the parliament context, and the
    full-scan heal calling _parl.healer.heal (not the nonexistent
    _parl.heal).

The LLM is stubbed at `core.parliament.llm_call._llm_call_protected`
(the same seam the existing V2 routing tests use). Production code is
never stubbed.
"""
import asyncio
import importlib
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.parliament.routing import should_use_single_model  # noqa: E402

VALID_PY = (
    "import os\nfrom pathlib import Path\n\n"
    "class FixedThing:\n    def run(self):\n"
    "        return {\n            'key': 'value',\n"
    "            'other': 'x',\n"
    "        }\n"
)

# Analysis content that scores above the CEO floor via _score_analysis
# (numbers + structure + actionable conclusion + >= 50 words), so the
# Council B test exercises the vote path instead of the judge path.
ANALYSIS_TEXT = (
    "## Analysis\n\n"
    "1. First finding: 42% of requests failed in the last 7 days, "
    "with 18 errors per hour at peak load across 3 regions.\n\n"
    "2. Second finding: latency rose 25% after the deploy on Monday, "
    "and 9 of 12 slow traces point at the same database query.\n\n"
    "3. Third finding: retry storms added 1200 extra calls per hour, "
    "which explains most of the 30% cost increase this week.\n\n"
    "We recommend adding a circuit breaker and we suggest capping "
    "retries at 2 per request as the next step for the team.\n"
)


def _parliament_with_stub(monkeypatch, content=VALID_PY):
    """Fresh Parliament with the protected LLM call stubbed to return
    valid code. Returns (parliament, calls) where calls records every
    invocation's kwargs."""
    from core import parliament as parl_mod
    importlib.reload(parl_mod)
    calls = []

    async def fake_llm_call_protected(**kwargs):
        calls.append(kwargs)
        return (content, 10.0, None)

    monkeypatch.setattr(parl_mod.llm_call, "_llm_call_protected",
                        fake_llm_call_protected)
    return parl_mod.Parliament(db=None), calls


def _run(parl, **context):
    ctx = {"task_type": "code_fix", "file_path": "x.py", "user_id": "u"}
    ctx.update(context)
    return asyncio.run(parl.run(task="fix x.py", context=ctx))


# ─── Single-model path ──────────────────────────────────────────────

def test_single_model_swift_pro_uses_one_call(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run(parl, single_model=True)
    assert decision["status"] == "success"
    assert decision["output"] == VALID_PY.strip()  # _strip_fences strips
    assert decision["single_model"] is True
    assert decision["circuit_breaker_fallback"] is False
    assert len(calls) == 1, f"single-model must make 1 call, got {len(calls)}"
    assert calls[0]["trace_name"] == "parliament.fallback_single"


def test_default_path_still_uses_three_calls(monkeypatch):
    """maxx / retry / default: no flag → full Council A vote, 3 calls."""
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run(parl)
    assert decision["status"] == "success"
    assert decision.get("single_model") is not True
    assert decision["circuit_breaker_fallback"] is False
    assert len(calls) == 3, f"full Council must make 3 calls, got {len(calls)}"


def test_single_model_flag_false_keeps_council(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run(parl, single_model=False)
    assert decision["status"] == "success"
    assert len(calls) == 3


def test_single_model_ignored_for_analysis(monkeypatch):
    """TaskRouter B/C selection is preserved: the flag only applies to
    code_fix (Council A). An analysis task still gets the 3-member
    Council B vote even with single_model=True."""
    parl, calls = _parliament_with_stub(monkeypatch, content=ANALYSIS_TEXT)
    decision = _run(parl, single_model=True, task_type="analysis")
    assert decision["status"] == "success"
    assert decision["council"] == "B"
    assert decision.get("single_model") is not True
    assert len(calls) == 3


def test_single_model_threads_max_tokens_override(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run(parl, single_model=True, max_tokens_override=20000)
    assert decision["status"] == "success"
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 20000


def test_single_model_caps_override_at_32000(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run(parl, single_model=True, max_tokens_override=50000)
    assert decision["status"] == "success"
    assert calls[0]["max_tokens"] == 32000


def test_single_model_defaults_to_4000_without_override(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch)
    decision = _run(parl, single_model=True)
    assert decision["status"] == "success"
    assert calls[0]["max_tokens"] == 4000


def test_single_model_failure_is_manual_review(monkeypatch):
    parl, calls = _parliament_with_stub(monkeypatch, content="")
    # Stub returns empty content with no error: _fallback_single_call
    # treats empty as failure (same as the breaker path).
    decision = _run(parl, single_model=True)
    assert decision["status"] == "manual_review"
    assert decision["output"] is None
    assert decision["single_model"] is True
    assert len(calls) == 1


# ─── Gate helper ────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,task_type,attempt,expected", [
    ("swift", "code_fix", 1, True),
    ("pro", "code_fix", 1, True),
    ("SWIFT", "code_fix", 1, True),
    ("maxx", "code_fix", 1, False),
    (None, "code_fix", 1, False),
    ("", "code_fix", 1, False),
    ("swift", "analysis", 1, False),
    ("swift", "code_fix", 2, False),   # retry escalates to Council
    ("pro", "code_fix", 2, False),
    ("maxx", "code_fix", 2, False),
])
def test_should_use_single_model_matrix(mode, task_type, attempt, expected):
    assert should_use_single_model(
        mode=mode, task_type=task_type, attempt=attempt) is expected


# ─── LoopEngine threading (source guards) ───────────────────────────
# The engine cannot be instantiated in this lane's tree (it needs the
# repo, Mongo, and the full router surface), so the threading is pinned
# by source: the field, the attempt passthrough, the context flag, and
# the heal call. Behaviour is proven at the Parliament level above.

def _loop_engine_src():
    return (BACKEND / "services" / "loop_engine.py").read_text()


def test_loop_engine_has_mode_field():
    src = _loop_engine_src()
    assert "self.mode: Optional[str] = None" in src


def test_loop_engine_passes_attempt_and_single_model_flag():
    src = _loop_engine_src()
    assert "async def _gen_via_parliament(path, attempt: int = 1):" in src
    assert "_gen_via_parliament(path, attempt=attempt)" in src
    assert '"single_model":    _single_model,' in src
    assert "should_use_single_model" in src


def test_loop_engine_compiles():
    import py_compile
    py_compile.compile(str(BACKEND / "services" / "loop_engine.py"),
                       doraise=True)


def test_full_scan_heal_uses_healer_not_missing_method():
    """loop_engine.py:2941 used to call _parl.heal(...) — a method
    Parliament does not have, so full-scan heal silently never ran
    (AttributeError swallowed). It must call _parl.healer.heal with
    the SelfHeal contract."""
    src = _loop_engine_src()
    fn = src.split("async def _heal_full_scan_findings")[1].split(
        "# ── Phase 5 — Ship")[0]
    assert "_parl.heal(" not in fn, "nonexistent Parliament.heal still called"
    assert "_parl.healer.heal(" in fn
    assert '"output": target.get("content") or ""' in fn
    assert 'heal_result.get("output")' in fn
    assert '(heal_result or {}).get("content")' not in fn
    assert "max_rounds=MAX_SELF_HEALS" in fn
