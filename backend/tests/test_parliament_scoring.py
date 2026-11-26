"""
Findings fix 2026-11-25 — Parliament scoring syntax gate + CEO defence.

Proves the CONFIRMED scoring bug is closed: truncated/broken code with
6+ keyword markers used to score a hardcoded 0.92 and auto-pass the CEO
floor (0.55). Now syntax validity is a NECESSARY condition for any score
>= the floor, and CEO.decide drops provably-broken winners even if a
stale/bypassed score arrives.

Runs against the real `core.parliament` package (no mocks of production
code — only the LLM judge is stubbed where decide() would call it).
"""
import asyncio
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.parliament.scoring import (  # noqa: E402
    _score_output, _syntax_invalid, _syntax_valid,
)
from core.parliament.ceo import CEO  # noqa: E402

# Verifier's exact case: 6 markers, unclosed `{`, unclosed def/class.
BROKEN_PY = (
    "import os\nfrom pathlib import Path\n\n"
    "class BrokenThing:\n    def run(self):\n"
    "        return {\n            \"key\": \"value\",\n"
)
VALID_PY = BROKEN_PY + '            "other": "x",\n        }\n'
# Same broken shape in JS (node --check rejects the unclosed brace).
BROKEN_JS = "function broken( {\n    const x = 1;\n    return {\n"
VALID_JS = (
    "function add(a, b) {\n    const x = a + b;\n    return x;\n}\n"
    "module.exports = { add };\n"
)
# Valid TypeScript — no parser here, so it must NOT auto-pass either.
VALID_TS = (
    "export function greet(name: string): string {\n"
    "    return 'hi ' + name;\n}\n"
)


def _votes(*items):
    return [
        {"member": m, "output": out, "score": s, "temp": t, "error": None}
        for m, out, s, t in items
    ]


# ─── The regression test (verifier's sketch) ─────────────────────────

def test_truncated_code_with_keywords_fails():
    """6 keyword markers + unclosed brace/def/class must NOT score 0.92
    (old behaviour) and must sit BELOW the CEO floor 0.55."""
    score = _score_output(BROKEN_PY, file_path="broken.py")
    assert score == 0.30, f"broken code must cap at 0.30, got {score}"
    assert score < 0.55


def test_truncated_code_with_keywords_fails_without_file_path():
    """Same verdict when the caller passes no file_path (language is
    then inferred: ast.parse fails, no JS parse saves it)."""
    assert _score_output(BROKEN_PY) < 0.55


def test_valid_code_keeps_keyword_tiers():
    """Positive control: the same snippet completed scores >= 0.78."""
    assert _score_output(VALID_PY, file_path="fixed.py") >= 0.78
    assert _score_output(VALID_PY, file_path="fixed.py") == 0.92


def test_one_keyword_garbage_fails():
    """1 marker used to score 0.60 and pass. `return` at module level
    is a SyntaxError, so it must now fail the gate."""
    assert _score_output("return " + "x" * 60, file_path="x.py") < 0.55


# ─── JS / TS ────────────────────────────────────────────────────────

def test_truncated_js_fails_node_check():
    assert _score_output(BROKEN_JS, file_path="broken.js") < 0.55


def test_valid_js_passes_node_check():
    assert _score_output(VALID_JS, file_path="add.js") >= 0.60


def test_valid_typescript_capped_below_floor():
    """No TS parser here: valid TS is UNVERIFIED, so it caps below the
    floor and the CEO judge decides instead of an auto-pass."""
    assert _score_output(VALID_TS, file_path="greet.ts") == 0.30


def test_syntax_helpers_tri_state():
    assert _syntax_valid(VALID_PY, file_path="a.py") is True
    assert _syntax_valid(BROKEN_PY, file_path="a.py") is False
    assert _syntax_invalid(BROKEN_PY, file_path="a.py") is True
    assert _syntax_invalid(VALID_PY, file_path="a.py") is False
    # Unverified language: neither valid nor (provably) invalid.
    assert _syntax_valid(VALID_TS, file_path="a.ts") is False
    assert _syntax_invalid(VALID_TS, file_path="a.ts") is False


# ─── CEO defence-in-depth ───────────────────────────────────────────

def test_ceo_decide_drops_broken_winner():
    """A single 0.92 vote whose output is broken Python must NOT return
    success — even though the score clears the floor. No judge call is
    needed: the filter empties the usable pool first."""
    ceo = CEO()
    judge_calls = []

    async def _never_judge(*a, **k):
        judge_calls.append(1)
        return None

    ceo._llm_judge = _never_judge
    decision = asyncio.run(ceo.decide(
        task="fix broken.py",
        votes=_votes(("A1", BROKEN_PY, 0.92, 0.1)),
        context={"task_type": "code_fix", "file_path": "broken.py",
                 "council": "A"},
    ))
    assert decision["status"] == "manual_review"
    assert decision["output"] is None
    assert "syntax gate" in decision["reasoning"]
    assert judge_calls == []


def test_ceo_decide_accepts_valid_winner():
    """Positive control: valid code at 0.92 still auto-passes, judge
    never consulted."""
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


def test_ceo_decide_picks_valid_over_broken():
    """Broken 0.92 + valid 0.78: the broken candidate is dropped from
    the pool and the valid one wins."""
    ceo = CEO()
    decision = asyncio.run(ceo.decide(
        task="fix it",
        votes=_votes(("A1", BROKEN_PY, 0.92, 0.1),
                     ("A2", VALID_PY, 0.78, 0.2)),
        context={"task_type": "code_fix", "file_path": "x.py",
                 "council": "A"},
    ))
    assert decision["status"] == "success"
    assert decision["winner"] == "A2"
    assert decision["output"] == VALID_PY


def test_ceo_decide_keeps_unverified_language_for_judge():
    """A stale/high score on an UNVERIFIED language (TS) is not dropped
    by the filter — only provably-broken code is. The real callers never
    produce such a score (_score_output caps TS at 0.30); this pins the
    deliberate boundary of the defence."""
    ceo = CEO()
    decision = asyncio.run(ceo.decide(
        task="fix greet.ts",
        votes=_votes(("A1", VALID_TS, 0.92, 0.1)),
        context={"task_type": "code_fix", "file_path": "greet.ts",
                 "council": "A"},
    ))
    assert decision["status"] == "success"
