"""
services/failure_taxonomy.py — Bug 8 fix (2026-09-17): 33.9% task
success rate (19/56) with no breakdown of WHY tasks fail. "Task
Health" showed a flat `Failed: 35` — not debuggable, not
prioritizable.

Deliberately SEPARATE from `core/errors.py::classify_exception()`,
which classifies by exception TYPE/STRUCTURE only (its own stated
design rule — never parse `str(exc)`, since messages may not be
English). That system already tags the generic "task crashed with
an unhandled exception" path (see the `except Exception as e` blocks
in worker_git.py/worker_api.py) — this module fills the gap for the
OTHER kind of failure: a pipeline GATE (hallucination check, syntax
check, design-lint check) that deliberately REJECTS a generation and
returns an error STRING with no exception at all, so
`classify_exception()` never even runs for it. Same orthogonal-
system precedent `services/error_translator.py` already documents
for text-based, after-the-fact classification of a message string
for display — this one is for grouping/counting, not display.

Best-effort ANALYTICS classifier, not a routing/control-flow
decision — if it mis-tags a message as UNKNOWN, nothing breaks; it's
strictly additive metadata for the "Task Health" breakdown and the
retry-policy lookup below.
"""
from __future__ import annotations

import re


class FailureReason:
    TIMEOUT = "TIMEOUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    CONTEXT_NOT_FOUND = "CONTEXT_NOT_FOUND"
    HALLUCINATION_GATE = "HALLUCINATION_GATE"
    USER_CANCELLED = "USER_CANCELLED"
    UNKNOWN = "UNKNOWN"


# Order matters — first match wins. Checked top-to-bottom against the
# exact, known error strings the gates in cto_pipeline_steps.py /
# worker_git.py / worker_api.py already produce.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"cancell?ed by user|user[\s_-]?cancell?ed|aborted by user", re.I),
     FailureReason.USER_CANCELLED),
    (re.compile(r"timed out|timeout|waited \d+\s*s(ec(ond)?s?)?\b", re.I),
     FailureReason.TIMEOUT),
    (re.compile(r"design linter blocked|lint(er)? blocked|policy blocked|blocked commit", re.I),
     FailureReason.POLICY_BLOCKED),
    (re.compile(
        r"don'?t see that (in|on)|couldn'?t read that file|"
        r"file not found|no such file|not found in your connected|"
        r"could not find (the )?file",
        re.I,
     ), FailureReason.CONTEXT_NOT_FOUND),
    (re.compile(
        r"does not match the real file|invented code|hallucinat|"
        r"syntax errors after auto-retry|kept producing content",
        re.I,
     ), FailureReason.HALLUCINATION_GATE),
]


def classify_failure_reason_from_text(error: str) -> str:
    """Best-effort reason code for a failure MESSAGE STRING (no
    exception object available — the gate returned an error string
    directly). Returns one of the `FailureReason` constants, defaulting
    to UNKNOWN rather than guessing."""
    text = error or ""
    for pattern, reason in _PATTERNS:
        if pattern.search(text):
            return reason
    return FailureReason.UNKNOWN


# Bug 8's explicit ask: "Retry policy matched to the failure class."
# TIMEOUT/CONTEXT_NOT_FOUND deserve an automatic retry; POLICY_BLOCKED/
# USER_CANCELLED must never blindly retry (deterministic — the same
# input fails again). HALLUCINATION_GATE already gets its one retry
# INSIDE the gate itself (see `_run_hallucination_gate`/
# `_run_syntax_gate`'s auto-retry-with-feedback) before this code is
# ever recorded, so by the time it reaches here, that budget is
# already spent — no further blind retry.
RETRY_POLICY: dict[str, dict] = {
    FailureReason.TIMEOUT:             {"retry": True,  "backoff_s": 2},
    FailureReason.CONTEXT_NOT_FOUND:   {"retry": True,  "backoff_s": 0.5},
    FailureReason.HALLUCINATION_GATE:  {"retry": False, "backoff_s": None},
    FailureReason.POLICY_BLOCKED:      {"retry": False, "backoff_s": None},
    FailureReason.USER_CANCELLED:      {"retry": False, "backoff_s": None},
    FailureReason.UNKNOWN:             {"retry": False, "backoff_s": None},
}
