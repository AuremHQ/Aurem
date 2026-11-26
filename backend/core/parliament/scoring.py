"""
core/parliament/scoring.py — 2026-09-08 Phase 3 god-class split.

Output scoring + type-detection helpers. Moved verbatim out of the
single core/parliament.py (zero logic change) — used by councils.py,
ceo.py, self_heal.py, and parliament.py.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile

_REFUSAL_RX = re.compile(
    r"\b(i (?:cannot|can't|won't|will not|am unable to)|"
    r"as an ai|i'm just an ai|i'm sorry, (?:but )?i can(?:not|'t))\b",
    re.IGNORECASE,
)

_FENCE_RX = re.compile(r"^```(?:[a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)\n```\s*$")


def _strip_fences(text: str) -> str:
    """Strip a single outer ```lang … ``` if present (LLMs love these)."""
    if not text:
        return ""
    s = text.strip()
    m = _FENCE_RX.match(s)
    return m.group(1) if m else s


# ─────────────────────────────────────────────────────────────────────
#  Findings fix 2026-11-25 — syntax gate for code_fix scoring.
#
#  `_score_output` used to accept any ≥50-char output containing a
#  single keyword substring (1 marker → 0.60 ≥ CEO floor 0.55). A
#  truncated file with 6 markers scored a hardcoded 0.92 and
#  Parliament reported "success" with broken code. Syntax validity
#  is now a NECESSARY condition for any score >= the floor: keyword
#  hits only RANK code that positively parses.
#
#  Two helpers because "cannot prove it parses" and "proved it is
#  broken" are different things:
#    _syntax_valid()   → True only if a parser positively accepts it.
#                        `_score_output` uses this: unverified code
#                        caps below the floor and the CEO LLM judge
#                        decides instead of an auto-pass.
#    _syntax_invalid() → True only if a parser positively rejects it
#                        for a positively-identified language.
#                        `CEO.decide` uses this to drop broken
#                        candidates from the judge pool. Unverified
#                        languages (TS/JSX/Go/...) are NOT dropped.
# ─────────────────────────────────────────────────────────────────────

_PY_EXTS = (".py",)
_JS_CHECK_EXTS = (".js", ".mjs", ".cjs")  # node --check parses these.
_JS_MARKERS = ("function ", "const ", "let ", "export ", "=>", "require(")


def _ext_of(file_path: str | None) -> str:
    return os.path.splitext(file_path or "")[1].lower()


def _parses_python(text: str) -> bool:
    """stdlib hard gate — always available, side-effect-free.
    `compile()` (not bare `ast.parse`): `ast.parse` alone accepts
    module-level `return`/`break` as legal nodes, while `compile()`
    rejects them — exactly the garbage that used to score 0.60.
    Compiling never executes the code."""
    try:
        compile(text, "<candidate>", "exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False
    return True


def _node_check(text: str, suffix: str) -> bool | None:
    """`node --check` on a temp file. True = valid syntax,
    False = syntax error, None = could not check (no node binary,
    timeout, I/O error) — callers must treat None as UNVERIFIED,
    never as broken."""
    node = shutil.which("node")
    if not node:
        return None
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="parliament-score-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        r = subprocess.run(
            [node, "--check", tmp],
            timeout=10, capture_output=True,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _looks_like_js(text: str) -> bool:
    return any(m in text for m in _JS_MARKERS)


def _syntax_valid(text: str, *, file_path: str | None = None) -> bool:
    """True only if a parser positively accepts the candidate."""
    ext = _ext_of(file_path)
    if ext in _PY_EXTS:
        return _parses_python(text)
    if ext in _JS_CHECK_EXTS:
        return _node_check(text, ext) is True
    if ext:
        # .ts/.tsx/.jsx/.go/.rs/... — no parser here; unverified.
        return False
    # No file context: accept if EITHER parser accepts it. A
    # truncated file fails both, which is the case that matters.
    if _parses_python(text):
        return True
    if _looks_like_js(text):
        return _node_check(text, ".js") is True
    return False


def _syntax_invalid(text: str, *, file_path: str | None = None) -> bool:
    """True only if a parser positively rejects the candidate for a
    positively-identified language. Unknown languages and missing
    tooling return False — never condemn on a guess."""
    ext = _ext_of(file_path)
    if ext in _PY_EXTS:
        return not _parses_python(text)
    if ext in _JS_CHECK_EXTS:
        return _node_check(text, ext) is False
    if not ext and _looks_like_js(text):
        return _node_check(text, ".js") is False
    return False


def _score_output(output: str, *, task_type: str = "code_fix",
                  expected_min_chars: int = 50,
                  file_path: str | None = None) -> float:
    """Heuristic 0.0-1.0 score for a candidate output."""
    if not output:
        return 0.0
    text = output.strip()
    if _REFUSAL_RX.search(text):
        return 0.0
    if len(text) < expected_min_chars:
        return 0.2
    # Iter 212m-155 — task-type-aware scoring.
    # Part XII group 1 (d2) — dispatch now covers the router's whole
    # map (routing.py _TASK_TYPE_TO_COUNCIL), not just three exact
    # strings. "write" (routed) vs "writing" (scored) meant Council C
    # tasks never reached _score_writing; code_review/security/
    # lint_heal and report/insight/summarize/email/copy/draft all
    # fell through to a flat 0.65, which auto-accepts (>= CEO floor
    # 0.55) without the judge ever seeing the output.
    if task_type in ("analysis", "report", "insight", "summarize"):
        return _score_analysis(text)
    if task_type in ("writing", "write", "email", "copy", "draft"):
        return _score_writing(text)
    if task_type in ("code_fix", "code_review", "security", "lint_heal"):
        markers = ("def ", "class ", "import ", "function ", "const ",
                   "let ", "return ", "{", "}", "from ", "export ")
        hits = sum(1 for m in markers if m in text)
        if not _syntax_valid(text, file_path=file_path):
            # Findings fix 2026-11-25 — broken/truncated (or
            # unverifiable-language) code must never auto-pass on
            # keywords alone. Cap at the old "no markers" tier, below
            # the CEO floor (0.55), so the CEO LLM judge decides.
            return 0.30
        if hits >= 6:
            return 0.92
        if hits >= 3:
            return 0.78
        if hits >= 1:
            return 0.60
        return 0.30
    # Unknown task type: no scorer owns it, so do NOT auto-accept on
    # a made-up 0.65. 0.50 sits just below the CEO floor (0.55) and
    # sends the candidate to the LLM judge instead.
    return 0.50


# ─────────────────────────────────────────────────────────────────────
#  Iter 212m-155 — structural scoring for Council B (analysis) and
#  Council C (writing).  Returns a 0.0-1.0 float on the same scale as
#  the code scorer so the CEO can compare apples-to-apples.
# ─────────────────────────────────────────────────────────────────────

_NUMBER_RX = re.compile(r"\d+\.?\d*%?")


def _has_signal(text_lower: str, signal: str) -> bool:
    """Part XII group 1 (d3) — word-boundary signal match. Bare
    substring matching misclassified: "class" in "classify", "bug"
    in "debug", "fix" in "prefix", "write" in "overwrite", "you" in
    "your"/"YouTube", "action" in "transaction"/"satisfaction".
    Multi-word signals ("next step", "reach out") match as phrases."""
    return re.search(r"\b" + re.escape(signal) + r"\b", text_lower) is not None


def _score_analysis(text: str) -> float:
    """Score analysis output by structural quality, not correctness."""
    score = 0.60
    # Has numbers / data points → +0.15
    if _NUMBER_RX.search(text):
        score += 0.15
    # Has clear structure (markdown headers or numbered lists or bullets)
    if any(tok in text for tok in ("## ", "1.", "2.", "- ")):
        score += 0.10
    # Length sanity.
    words = len(text.split())
    if words < 50:
        score -= 0.20
    elif words > 500:
        score -= 0.10
    # Has actionable conclusion.
    action_signals = ("recommend", "suggest", "should",
                      "consider", "next step", "action")
    if any(_has_signal(text.lower(), s) for s in action_signals):
        score += 0.15
    return max(0.0, min(1.0, score))


def _score_writing(text: str) -> float:
    """Score writing/copy output by structural quality."""
    score = 0.60
    words = len(text.split())
    if 30 <= words <= 200:
        score += 0.15
    elif words > 300:
        score -= 0.15
    elif words < 20:
        score -= 0.20
    cta_signals = ("reply", "click", "schedule", "book", "call",
                   "reach out", "let me know", "interested", "response")
    if any(_has_signal(text.lower(), s) for s in cta_signals):
        score += 0.15
    # Weak opening: starting with "I " bleeds the reader-focus.
    if text.lstrip().startswith("I "):
        score -= 0.10
    personal_signals = ("you", "your", "you're")
    if any(_has_signal(text.lower(), s) for s in personal_signals):
        score += 0.10
    return max(0.0, min(1.0, score))


# ─────────────────────────────────────────────────────────────────────
#  GAP 3 — Output-type detection + CEO temperature mapping
# ─────────────────────────────────────────────────────────────────────

CEO_TEMPS: dict[str, float] = {
    "code_output":     0.0,
    "json_output":     0.0,
    "tool_call":       0.0,
    "analysis_output": 0.3,
    "plan_output":     0.4,
    "writing_output":  0.65,
    "casual_output":   0.7,
}

OUTPUT_TYPE_SIGNALS: dict[str, tuple[str, ...]] = {
    "code_output":     ("fix", "patch", "implement", "refactor",
                        "write code", "function", "class", "bug",
                        "rewrite", "vulnerability", "exploit"),
    "json_output":     ("json", "schema", "structured", "format as"),
    "analysis_output": ("analyze", "report", "summary", "insights",
                        "what is", "how many", "show me", "summarize",
                        "summarise"),
    "writing_output":  ("email", "write", "draft", "message",
                        "outreach", "copy"),
}


def detect_output_type(task: str, *, council: str | None = None) -> str:
    """Explicit output-type classifier driving the CEO's temperature.

    Counts keyword matches across each registered output type and
    picks the highest-scoring class.  Ties resolve to `code_output`
    when the council is A (safer to default to t=0.0 for code
    contexts), otherwise to `analysis_output`."""
    task_lower = (task or "").lower()
    if not task_lower:
        return "code_output" if council == "A" else "analysis_output"
    scores = {k: 0 for k in OUTPUT_TYPE_SIGNALS}
    for output_type, signals in OUTPUT_TYPE_SIGNALS.items():
        for signal in signals:
            if _has_signal(task_lower, signal):
                scores[output_type] += 1
    best, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score == 0:
        return "code_output" if council == "A" else "analysis_output"
    return best
