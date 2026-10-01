"""
services/turn_outcome.py — what actually happened in a chat turn.

Four outcomes, decided from evidence the server holds (tool results, a
stored/executed pending change), never from how the reply sounds:

  DONE         at least one real tool call succeeded, or a pending change
               was executed. `evidence` lists them.
  NEEDS_YOU    a change is waiting for approval, or ORA asked a question
               the work is blocked on.
  NOTHING_DONE no tool result and no change. `reason` says why:
               answered | announced_not_done | nothing_pending | cancelled.
  FAILED       the turn errored or produced no reply.

See docs/CHAT_OUTCOME_CONTRACT.md for how to add a new action safely.
"""
from __future__ import annotations

import re

DONE, NEEDS_YOU, NOTHING_DONE, FAILED = "DONE", "NEEDS_YOU", "NOTHING_DONE", "FAILED"

# Deterministic commit-boundary providers (services/actions/pending_action.py).
_PROVIDER_OUTCOMES = {
    "commit-boundary-executor": (DONE, "change_executed"),
    "commit-boundary-executing": (DONE, "change_executing"),
    "commit-boundary-disambiguate": (NEEDS_YOU, "which_change"),
    "commit-boundary-no-pending": (NOTHING_DONE, "nothing_pending"),
    "commit-boundary-cancelled": (NOTHING_DONE, "cancelled"),
    "commit-boundary-cancel-noop": (NOTHING_DONE, "nothing_pending"),
    "commit-boundary-error": (FAILED, "change_failed"),
}

# A reply that commits to doing work later, with no tool result behind it.
_PROMISE_RE = re.compile(
    r"\b(?:i['’]ll|i will|i['’]m going to|let me|let['’]s|then i['’]ll)\b[^.\n]{0,60}"
    r"\b(?:creat|build|draft|writ|submit|open|push|commit|updat|fix|finish|assembl|"
    r"generat|run|scan|check|implement|set up)\w*", re.I)
_STALL_RE = re.compile(r"\b(?:stand\s?by|one moment|building that now)\b", re.I)


def _real_tool_calls(result: dict) -> list[str]:
    names = []
    for inv in result.get("tool_invocations") or []:
        if isinstance(inv, dict) and inv.get("ok") and not inv.get("forced"):
            names.append(str(inv.get("tool") or inv.get("name") or "tool"))
    return names


def decide(result: dict, content: str, state: str) -> dict:
    """`state` is the transport/validation state already computed by
    interaction_contract.finalize (success|partial|blocked|error)."""
    if state == "error":
        return {"outcome": FAILED, "reason": "turn_error", "evidence": []}
    if state == "blocked":
        return {"outcome": NEEDS_YOU, "reason": "clarification_needed", "evidence": []}
    if "```aurem-handoff" in content:
        return {"outcome": NEEDS_YOU, "reason": "change_awaiting_approval",
                "evidence": ["proposed change"]}
    if state == "partial":
        return {"outcome": NOTHING_DONE, "reason": "announced_not_done", "evidence": []}
    mapped = _PROVIDER_OUTCOMES.get(str(result.get("provider") or ""))
    if mapped:
        return {"outcome": mapped[0], "reason": mapped[1], "evidence": [mapped[1]]}
    tools = _real_tool_calls(result)
    if tools:
        return {"outcome": DONE, "reason": "tool_result", "evidence": tools}
    if _PROMISE_RE.search(content) or _STALL_RE.search(content):
        return {"outcome": NOTHING_DONE, "reason": "announced_not_done", "evidence": []}
    return {"outcome": NOTHING_DONE, "reason": "answered", "evidence": []}
