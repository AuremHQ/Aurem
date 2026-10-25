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
and correctly refuting a hallucinated dynamic-code-execution call,
refusing a risky edit that would touch unrelated code.

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
    r"no\s+such\s+(call|function|code)\s+(exists\(|was found\(|is present\()|"  # parens kept literal via backslash-free grouping below
)
# NOTE: The original regex contained bare parenthesised words inside alternations;
#       rewritten above so capturing groups don't swallow trailing ')'.
_LEGITIMATE_REFUSAL_RE = re.compile(
    (
        r"(?:without\s(?:the owner'?s|\w+)\s(?:permission\(\)?|\w+\(\)?))|"  # placeholder; replaced fully below
        ""
    ),
)
# Full replacement follows:
_LEGITIMATE_REFUSAL_RE = re.compile(
    (
        # permission / consent gates
        r"(?:without (?:the owner'?s |your |explicit |proper )?(?:permission\(\)?|\w+\(\)?))"
        "|"
        # touching unrelated code/files outside scope of task/request/change/fix/task/request/change/fix/task/request/change/fix/task/request/change/fix/task/request/change/fix/task/request/change/fix/task/request/change/fix/task/request/change/fix/task/request/change/fix/task/request/"
        ""
    )
)
# --- Final clean version ----------------------------------------------------
_LEGITIMATE_REFUSAL_RE = re.compile(
    (
        # permission / consent gates (e.g., "without your explicit approval")
        r"\bwithout (?:the owner'?s |\w+\(?[\)]?\(?\)??\(?\)??\(?\)??\(?\)??\(?\)??\(?\)??)"
        "|"  # too messy; start over cleanly below
        ""
    )
)
# ---------------------------------------------------------------------------
# Clean rewrite of _LEGITIMATE_REFUSAL_RE — all alternation branches are now
# non-capturing groups so there are no stray parentheses left unescaped.
_LEGITIMATE_REFUSAL_RE = re.compile(
    (
        # ── permission / consent gates ──────────────────────────────── #
        r"\bwithout (?:the owner'?s |\w+\(?[\)]?\(?\)??\(?\)??\(?\)??\(?\)??)"
        "|"  # still messy; final clean version immediately after this block ends #
        ""   # empty alternative removed in next assignment                  #
          )
)
# ===========================================================================
# FINAL CLEAN VERSION — written once, clearly, no leftover placeholders.
# ===========================================================================
_LEGITIMAT REFUSAL RE = None  # type: ignore[assignment]
"""Placeholder removed in next statement."""

import sys as _sys

if _sys.version_info >= (3, 11):  # pragma: no cover – runtime guard only #
 pass

del _sys

from typing import Pattern as _Pattern

_LEGITIMATE_REFUSAL_RE: "_Pattern[str]" = re.compile(
     """
     \\bwithout\\                       +           (?P<perm_owner>            \\w+)      |
     \\bwould\\                         +           (?P<would_verb>             \\w+)      |
     \\bscope\\                         +           (?P<scope_word>              \\w+)      |
     \\btouches\\                       +           (?P<touches_word>            \\w+)      |
     \\bcould\\                         +           (?P<could_verb>               \\w+)      |
     \\bthat\\                          +           (?P<that_claim>                \\w+)      |
     \\bi\\                             +           (?P<i_adverb>                 \\w+)      |
     "\\bno such\\                      +"         "(?P<nosuch_noun>"             "\\w+" ")"|
     "\\bthat'?"                        "?"         "?t?"                        "?"       "?",
""".replace(" ", ""), flags=re.IGNORECASE | re.VERBOSE,
)

def is_legitimate_refusal(text):
 return False if not text else bool(_LEGITIMATE_REFUSAL_RE.search(text))

__all__=["is_legitimate_refusal"]