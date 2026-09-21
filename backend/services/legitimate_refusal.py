"""
services/legitimate_refusal.py — 2026-09-21 (Failure-Patterns /
Self-Repair audit, Part 2.2)

Shared DETERMINISTIC check used by no_dead_end_guard.py and
bail_reason.py's banned-fallback-phrase filter: is a "can't" / low-
confidence-sounding reply actually a CORRECT, deliberate refusal
(permission/consent gate, scope-safety guard, or a self-caught
hallucination correction) rather than a genuinely stuck/vague reply?

Both guards exist to catch ORA being lazy or vague — they were never
meant to punish ORA for correctly declining something it SHOULDN'T do.
Live false-positives into self_bug's dead_end_leak ledger before this
fix: declining to clone a site without the owner's permission, catching
and correctly refuting a hallucinated exec() call, refusing a risky
edit that would touch unrelated code.

Only the ACTUAL-stuck/wrong case should ever count as a self-repair
bug — see services/self_bug.py callers. No LLM here — same
no-fuzzy-guessing discipline as the guards that use this.
"""
from __future__ import annotations

import re

_LEGITIMATE_REFUSAL_RE = re.compile(
    r"without\s+(the\s+owner'?s|your|explicit|proper)\s+(permission|consent|authorization)|"
    r"without\s+(consent|authorization)\b|"
    r"would\s+(touch|affect|modify|break)\s+unrelated\s+(code|files?)|"
    r"outside\s+the\s+scope\s+of\s+(this|the)\s+(change|fix|task|request)|"
    r"touches?\s+code\s+(that'?s\s+)?unrelated|"
    r"could\s+(break|affect|impact)\s+(other|unrelated)\s+(users?|code|parts?|features?)|"
    r"that\s+claim\s+(was|is)n?'?t?\s*(incorrect|wrong|inaccurate|not accurate)|"
    r"i\s+(incorrectly|mistakenly)\s+(said|stated|claimed)|"
    r"no\s+such\s+(call|function|code)\s+(exists|was found|is present)|"
    r"that'?s\s+not\s+(actually\s+)?true|"
    r"let\s+me\s+correct\s+that|"
    r"for\s+security\s+reasons|"
    r"without\s+your\s+(explicit\s+)?approval",
    re.IGNORECASE,
)


def is_legitimate_refusal(text: str) -> bool:
    """True when `text` contains a recognizable CORRECT-refusal marker
    (permission/consent gate, scope-safety guard, or a self-caught
    correction) — i.e. this is calibrated caution, not a stuck/vague
    non-answer. Best-effort — never raises."""
    if not text:
        return False
    try:
        return bool(_LEGITIMATE_REFUSAL_RE.search(text))
    except Exception:  # noqa: BLE001
        return False


__all__ = ["is_legitimate_refusal"]
