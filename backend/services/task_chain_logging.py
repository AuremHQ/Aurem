"""
services/task_chain_logging.py — Chain-of-Thought pattern analysis
(2026-09-08 · "make AUREM self-improving").

THIS IS STRUCTURED LOGGING, NOT RAW CHAIN-OF-THOUGHT. Every ORA
attempt (chat turn / agentic task) appends ONE compact, truncated
record to `task_chains`:

    task_type, user_prompt (<=200c), model_plan (<=500c),
    tool_calls (<=10), tool_sequence (<=10), final_response (<=500c),
    success, failure_reason, success_pattern

`sequential_thinking` tool calls already log METADATA-ONLY
unconditionally (see services/sequential_thinking_skills.py —
thought_number/total_thoughts/is_revision/branch_id, never persisted
across a turn). The raw reasoning TEXT of those thoughts is NEVER
persisted on success (storage/cost waste — metadata already tells us
"the model revised"). On FAILURE ONLY, the actual thought text
(redacted, each truncated to ~2000 chars) is additionally attached to
this SAME record as `failure_thoughts_truncated` — that's the only
place "why it failed" (not just "it revised") is mineable.

DO NOT: log raw/untruncated prompts or tool results, log secrets
(best-effort regex redaction below), or build anything beyond simple
group-by analysis on top of this collection (see
routers/admin_analytics.py's `/admin/failure-patterns`).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

TASK_TYPES = ("bug-fix", "feature", "refactor", "scan", "other")

_BUG_FIX_KEYWORDS = (
    "fix", "bug", "error", "broken", "crash", "doesn't work",
    "not working", "isn't working", "issue", "failing",
)
_SCAN_KEYWORDS = (
    "scan", "list every", "list all", "audit", "find all",
    "search for", "check all", "go through",
)
_REFACTOR_KEYWORDS = (
    "refactor", "clean up", "cleanup", "restructure", "reorganize",
    "reorganise", "simplify", "split up", "extract",
)
_FEATURE_KEYWORDS = (
    "add ", "build ", "create ", "implement ", "new feature",
    "make a", "set up", "integrate",
)


def classify_task_type(prompt: str) -> str:
    """Cheap keyword heuristic — deliberately not an ML classifier
    (DO-NOT: over-engineer the analysis)."""
    p = (prompt or "").lower()
    if any(k in p for k in _SCAN_KEYWORDS):
        return "scan"
    if any(k in p for k in _BUG_FIX_KEYWORDS):
        return "bug-fix"
    if any(k in p for k in _REFACTOR_KEYWORDS):
        return "refactor"
    if any(k in p for k in _FEATURE_KEYWORDS):
        return "feature"
    return "other"


# ── Secret redaction — same shapes as services/security_text_scanner.py's
# secret_leak rules, plus a couple of generic key=value / Bearer catches
# for free-form reasoning text (not source code) ──────────────────────
_SECRET_PATTERNS = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-_.]{20,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*"
        r"['\"]?[A-Za-z0-9_\-./+]{8,}['\"]?",
    ),
]


def redact_secrets(text: str) -> str:
    """Best-effort — never raises, never blocks logging."""
    if not text:
        return text
    out = text
    try:
        for pat in _SECRET_PATTERNS:
            out = pat.sub("[REDACTED]", out)
    except Exception:  # noqa: BLE001
        return text
    return out


def _truncate(s: Optional[str], n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "…[truncated]"


def _tool_sequence(tool_calls: Optional[list]) -> list[str]:
    names = []
    for tc in (tool_calls or [])[:10]:
        if isinstance(tc, str):
            names.append(tc)
            continue
        names.append((tc or {}).get("tool") or (tc or {}).get("name") or "?")
    return names


def _derive_model_plan(sequential_thoughts, tool_calls, final_response) -> str:
    """'What the model planned' — approximated from (in priority
    order): its own recorded sequential-thinking steps, else the
    order it called tools in, else the final answer itself (a
    single-shot reply has no separate plan)."""
    if sequential_thoughts:
        joined = " → ".join(
            (t.get("thought") or "")[:120] for t in sequential_thoughts[:5]
        )
        return _truncate(joined, 500)
    seq = _tool_sequence(tool_calls)
    if seq:
        return _truncate(f"Called tools in order: {', '.join(seq)}", 500)
    return _truncate(final_response or "", 500)


def _derive_success_pattern(tool_calls, final_response) -> str:
    seq = _tool_sequence(tool_calls)
    if seq:
        return _truncate(f"Resolved via {len(seq)} tool call(s): {', '.join(seq)}", 500)
    return "Direct answer, no tools needed"


async def log_task_chain(
    db, *, user_id: str, session_id: str, project_id: Optional[str],
    user_prompt: str, tool_calls: Optional[list], final_response: str,
    turn_state: Optional[str], failure_mode: Optional[str] = None,
    sequential_thoughts: Optional[list] = None,
) -> None:
    """Best-effort — appends ONE structured record to `task_chains`.
    Never raises (logging must never break the turn it's logging)."""
    if db is None:
        return
    try:
        success = (turn_state or "success") == "success"
        tool_seq = _tool_sequence(tool_calls)
        record = {
            "user_id":        user_id,
            "session_id":     session_id,
            "project_id":     project_id,
            "task_type":      classify_task_type(user_prompt),
            "user_prompt":    _truncate(redact_secrets(user_prompt or ""), 200),
            "model_plan":     _derive_model_plan(sequential_thoughts, tool_calls, final_response),
            "tool_calls":     tool_seq,
            "tool_sequence":  tool_seq,
            "final_response": _truncate(redact_secrets(final_response or ""), 500),
            "success":        success,
            "failure_reason": None if success else (failure_mode or turn_state or "unknown"),
            "success_pattern": _derive_success_pattern(tool_calls, final_response) if success else None,
            "ts": time.time(),
        }
        # COH persistence (failure-only) — see module docstring. Metadata
        # for EVERY sequential_thinking call already lands unconditionally
        # in `sequential_thinking_calls` via log_sequential_thinking_call;
        # the actual reasoning TEXT only ever gets this second, truncated
        # home when the turn genuinely failed.
        if not success and sequential_thoughts:
            record["failure_thoughts_truncated"] = [
                _truncate(redact_secrets(t.get("thought") or ""), 2000)
                for t in sequential_thoughts[:20]
            ]
        await db.task_chains.insert_one(record)
    except Exception as e:  # noqa: BLE001
        logger.debug("log_task_chain failed (non-blocking): %r", e)


__all__ = [
    "TASK_TYPES", "classify_task_type", "redact_secrets", "log_task_chain",
]
