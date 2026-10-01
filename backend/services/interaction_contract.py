"""Opt-in interaction boundary. Transport completion is not proof of work/review.

V1 is deliberately conservative: it does not claim to verify arbitrary semantics.
Legacy replies remain unchanged unless INTERACTION_CONTRACT_V1=1.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid

from fastapi import HTTPException

_NO_WRITE = re.compile(
    r"\b(?:do not|don't|don’t|never)\s+(?:touch|change|modify|edit|write|update|delete)"
    r"\s+(?:anything|(?:(?:any|the)\s+)?(?:files?|code)|this repo)\b", re.I)


def enabled() -> bool:
    return os.environ.get("INTERACTION_CONTRACT_V1") == "1"


def _instructions(prompt: str) -> str:
    # Quoted examples, source code and attached evidence are not instructions.
    from services.mode_b_council import _strip_attachment_blocks
    text = _strip_attachment_blocks(prompt or "")
    return re.sub(r'```[\s\S]*?```|"[^"\n]*"|`[^`\n]*`', '', text)


def factual_lookup(prompt: str) -> bool:
    """Bounded factual/status boundary, not a general intent classifier."""
    text = _instructions(prompt)
    parts = [re.sub(r'^\s*\d+[.)]\s*', '', s).strip()
             for s in re.split(r'[\n?]+', text) if s.strip()]
    return bool(parts) and all(re.match(
        r"^(?:what\s+(?:is|are|was|were)|which\s+.+\s+(?:is|are)|"
        r"how many|where\s+(?:is|are)|has\s+.+|have\s+.+|"
        r"(?:council|audit|error)\s+(?:queue|status|count)|"
        r"(?:show|list|report)\s+(?:the\s+)?(?:current\s+)?(?:status|counts))\b",
        s, re.I) for s in parts)


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


def read_only_request(prompt: str) -> bool:
    return factual_lookup(prompt) or bool(_NO_WRITE.search(_instructions(prompt)))


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
    elif state not in {"success", "partial", "blocked", "error"}:
        state = "success"
    from services.turn_outcome import decide
    verdict = decide(result, content, state)
    return {
        "version": 1, "request_id": request_id, "session_id": session_id,
        "request_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "turn_state": state,
        # `outcome` is decided from evidence (services/turn_outcome.py); a
        # delivered reply alone is never a completion.
        **verdict,
        "completion_basis": verdict["reason"] if state == "success" else state,
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


def new_request_id() -> str:
    return uuid.uuid4().hex