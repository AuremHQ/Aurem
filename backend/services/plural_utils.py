"""
services/plural_utils.py — Bug 5 fix (2026-09-17): "Parallel codegen
(1 agents)" instead of "1 agent". Root cause was naive string
concatenation with no plural handling for count == 1
(`f"... ({agents_count} agents) ..."`).

One tiny, shared formatter for counted labels — every "N word(s)"
string in the codebase should route through this instead of hand-
rolling `f"{n} {word}s"`, which silently breaks at n == 1 every time.
"""
from __future__ import annotations


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """`pluralize(1, "agent")` -> "1 agent"; `pluralize(3, "agent")` ->
    "3 agents". `plural` defaults to `singular + "s"` for the common
    case; pass it explicitly for irregular forms (e.g. "finding" /
    "findings" is regular, but "child" / "children" is not)."""
    word = singular if count == 1 else (plural if plural is not None else f"{singular}s")
    return f"{count} {word}"
