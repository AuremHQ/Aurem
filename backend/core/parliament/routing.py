"""
core/parliament/routing.py — 2026-09-08 Phase 3 god-class split.

`TaskRouter`. Moved verbatim out of the single core/parliament.py
(zero logic change).
"""
from __future__ import annotations

import re


def _count_keywords(text: str, keywords) -> int:
    """Word-boundary keyword hits. A multi-word keyword ("cover
    letter") matches as a phrase; a single word never matches inside
    a longer word ("fix" not in "prefix", "test" not in "latest")."""
    return sum(
        1 for k in keywords
        if re.search(r"\b" + re.escape(k) + r"\b", text)
    )


class TaskRouter:
    """Picks which Council should handle a given task.

    Iter 212m-160 — task_type routing:
      • analysis / report / insight / summarize → Council B (GLM-5.2 + DeepSeek rescue)
      • email / copy / write / draft            → Council C (DeepSeek, creative voice)
      • code_fix / code_review / security / lint_heal → Council A (LongCat → Claude)
      • everything else                          → Council A (safe default)

    `context["council"]` still wins when explicitly set so unit tests
    and any future caller that wants to pin a specific council can do
    so without losing the keyword path.
    """

    _COUNCIL_A_KEYWORDS = (
        "code", "patch", "fix", "refactor", "implement",
        "security", "vuln", "vulnerability", "scan", "lint", "syntax",
        "rewrite", "test", "validate",
    )

    # Part XII group 1 (a) — the B/C keyword lists did not exist: the
    # keyword branch and the fallthrough both returned "A", so B/C
    # were reachable only via the task_type map or the ctx pin. These
    # mirror the docstring above (analysis-flavoured → B,
    # writing-flavoured → C).
    _COUNCIL_B_KEYWORDS = (
        "analyse", "analyze", "analysis", "classify", "report",
        "insight", "insights", "summarize", "summarise", "summary",
        "findings", "metrics", "compare", "comparison", "evaluate",
        "evaluation", "trends", "breakdown",
    )
    _COUNCIL_C_KEYWORDS = (
        "email", "copy", "write", "draft", "outreach", "newsletter",
        "blog", "caption", "message", "cover letter", "subject line",
    )

    # Iter 212m-160 — explicit task_type → council map. Keys mirror the
    # strings already used by callers (`code_fix`, `code_review`,
    # `security`, `lint_heal`); the new B/C entries unlock Council B's
    # GLM-5.2 V2 swap + Council C's creative DeepSeek path for analysis
    # and writing tasks respectively.
    _TASK_TYPE_TO_COUNCIL = {
        # Council A — code surgery
        "code_fix":     "A",
        "code_review":  "A",
        "security":     "A",
        "lint_heal":    "A",
        # Council B — analysis / advisory (V2 GLM-5.2 + DeepSeek rescue)
        "analysis":     "B",
        "report":       "B",
        "insight":      "B",
        "summarize":    "B",
        # Council C — creative / writing
        "email":        "C",
        "copy":         "C",
        "write":        "C",
        "draft":        "C",
    }

    def route(self, task: str, context: dict | None = None) -> str:
        ctx = context or {}
        forced = ctx.get("council")
        if forced in ("A", "B", "C"):
            return forced
        ttype = (ctx.get("task_type") or "").lower()
        if ttype in self._TASK_TYPE_TO_COUNCIL:
            return self._TASK_TYPE_TO_COUNCIL[ttype]
        # Part XII group 1 (a) — keyword routing can now return B/C.
        # Word-boundary matching (not bare substring): "fix" in
        # "prefix" and "test" in "latest" must not route to A, and a
        # mixed task ("fix the report") scores on both sides instead
        # of the first substring winning. Most keyword hits wins;
        # ties and no-hits fall back to A (the safe default).
        text = (task or "").lower()
        hits = {
            "A": _count_keywords(text, self._COUNCIL_A_KEYWORDS),
            "B": _count_keywords(text, self._COUNCIL_B_KEYWORDS),
            "C": _count_keywords(text, self._COUNCIL_C_KEYWORDS),
        }
        best = max(hits, key=lambda k: hits[k])
        if hits[best] == 0:
            return "A"
        if list(hits.values()).count(hits[best]) > 1:
            return "A"
        return best


def should_use_single_model(*, mode: str | None, task_type: str | None,
                            attempt: int = 1) -> bool:
    """Findings fix 2026-11-25 — single-model shortcut gate.

    Pure decision, no I/O, so LoopEngine and tests can call it
    without the LLM stack. `mode` is the user's mode pill as set on
    the LoopEngine by the router (None = unknown → full Council).

      • swift/pro + code_fix + first attempt → True (1 call).
      • maxx, unknown/None mode, non-code task_type → False.
      • attempt >= 2 (bounded retry) → False: a failed single-model
        attempt escalates to the full Council.
    """
    if attempt != 1:
        return False
    if (mode or "").lower() not in ("swift", "pro"):
        return False
    return (task_type or "").lower() == "code_fix"
