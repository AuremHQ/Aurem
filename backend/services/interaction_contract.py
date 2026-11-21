"""Opt-in interaction boundary. Transport completion is not proof of work/review.

V1 is deliberately conservative: it does not claim to verify arbitrary semantics.
Legacy replies remain unchanged unless INTERACTION_CONTRACT_V1=1.
"""
from __future__ import annotations

import hashlib
import re
import uuid

from fastapi import HTTPException

from services.interaction_flags import (
    _NO_WRITE,
    _instructions,
    enabled,
    factual_lookup,
    read_only_request,
)

def request_conflict(prompt: str) -> None:
    """Reject explicit simultaneous no-write/write directives before dispatch.

    Does not pretend to resolve arbitrary natural-language contradictions.
    Rejection is visible, not a hidden choice of either instruction.
    """
    if not enabled():
        return
    text = _instructions(prompt)
    prohibition = _NO_WRITE.search(text)
    clauses = re.split(r'(?:[.!?;\n]+|\b(?:but|and)\b)\s*', text)
    mutation = any(re.match(
        r'^\s*(?:\d+[.)]\s*)?(?:please\s+)?(?:now\s+)?'
        r'(?:update|edit|modify|change|delete|write|patch|replace|commit|push|deploy)\b',
        clause, re.I) for clause in clauses)
    if prohibition and mutation:
        raise HTTPException(409, detail=(
            "Conflicting instructions: you prohibited changes and also requested a change. "
            "I have not executed either instruction or chosen one silently. "
            "Should this be read-only, or should I make the requested change?"))


def finalize(result: dict, content: str, *, prompt: str, session_id: str,
             request_id: str, low_confidence: bool = False) -> dict | None:
    """One envelope consumed by HTTP, SSE, persistence and UI. No provider inference."""
    if not enabled():
        return None
    from services.orchestrator import _is_intent_only_announcement
    state = result.get("turn_state")
    if result.get("error") or result.get("ok") is False or not content.strip():
        state = "error"
    elif low_confidence or content.startswith("I couldn't validate this response"):
        state = "blocked"
    elif "That was my next planned step" in content or _is_intent_only_announcement(content):
        state = "partial"
    elif _promises_a_tool_action_it_never_ran(content, result):
        # 2026-10 — C2 fix, second half. A model reply that still
        # carries an unexecuted tool-call tag (LongCat's own
        # `<longcat_tool_call>...` or any generic `<tool_call>` /
        # `<function_call>` the pipeline never ran) must not close as
        # "Response delivered" — same promised-and-never-run class as
        # the intent-only-announcement check above, just a structural
        # signal (a tag literally present with zero tool_calls_run)
        # instead of a text-phrase heuristic. Defense in depth:
        # `_call_longcat` already swaps this content out before it
        # gets here, but any OTHER path that lets a tag through must
        # not silently report success either.
        state = "partial"
    elif state not in {"success", "partial", "blocked", "error"}:
        state = "success"
    return {
        "version": 1, "request_id": request_id, "session_id": session_id,
        "request_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "turn_state": state,
        "completion_basis": "response_delivered" if state == "success" else state,
        "continue_available": state == "partial",
        "failure_mode": result.get("failure_mode"),
        "recovery_action": "continue" if state == "partial" else (
            "clarify" if state == "blocked" else "retry" if state == "error" else None),
        # Neither a council label nor a requested review mode proves a review.
        # Existing producers lack a content-bound review receipt. Explicit unknown.
        "review_evidence": None,
        "suggested_actions": [],
        "verified_paths": list(result.get("verified_paths") or []),
    }


def _promises_a_tool_action_it_never_ran(content: str, result: dict) -> bool:
    """True iff `content` still contains an unexecuted tool-call tag
    (any of `<tool_call>`, `<function_call>`, `<tool>`, `<function>`,
    the provider's plain-text `[ILLEGAL FUNCTION CALL]` marker,
    or LongCat's native `<longcat_tool_call>`) AND no tool actually
    ran this turn (`tool_calls_run == 0`). A tag present with real
    invocations alongside it (`tool_calls_run > 0`) is not this bug —
    that's a different model genuinely running one tool while also
    leaving stray text; THIS check is specifically "promised, zero
    delivered"."""
    if int(result.get("tool_calls_run") or 0) > 0:
        return False
    text = content or ""
    if not text:
        return False
    import re as _re
    return bool(_re.search(
        r"<\s*/?\s*(?:tool_call|function_call|tool|function|longcat_tool_call)\b"
        r"|\[\s*ILLEGAL\s+FUNCTION\s+CALL\s*\]",
        text, _re.IGNORECASE,
    ))


def new_request_id() -> str:
    return uuid.uuid4().hex