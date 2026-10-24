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
fix: declining to clone a site without the owner's permission,
catching and correctly refuting an unsafe dynamic-code-eval claim,
refusing an edit that would touch unrelated code.

Only the ACTUAL-stuck/wrong case should ever count as self-repair bug — see services/self_bug.py callers.
No LLM here — same no-fuzzy-guessing discipline as guards that use this.
"""
from __future__ import annotations

import re

_LEGITIMATE_REFUSAL_RE = re.compile(
    r"without\s+(the\s+owner'?s|your|explicit|proper)\s+(permission|consent|authorization)|"
    r"without\s+(consent|authorization)\b|"
    r"would\s+(touch|affect|modify|break)\s+unrelated\s+(code|files?)|"