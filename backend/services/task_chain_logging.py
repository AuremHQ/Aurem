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
import uuid
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


# ── Output-quality degeneration detector (2026-09-11) ─────────────────
# Founder live-caught: a "scan the whole repo" turn logged as 100%
# SUCCESS in task_chains even though its actual reply contained the
# known exact-phrase degeneration bug ("your page" repeated 20+ times
# in one table cell) — turn_state only tracks "did it terminate",
# never "was the text itself garbled". This is a narrow, cheap
# heuristic for that ONE known failure shape (NOT a general quality
# classifier — DO-NOT: over-engineer): a short phrase (1-5 words)
# repeated 8+ times back-to-back, allowing typical table/list
# separators (whitespace, commas, pipes, dashes) between repeats.
_REPETITION_DEGENERATION_RE = re.compile(
    r"\b(\w+(?:\s+\w+){0,4})\b(?:[\s,;|/\-]+\1\b){7,}",
    re.IGNORECASE,
)


def detect_output_repetition_degeneration(text: str) -> bool:
    """True if `text` contains the same short phrase repeated 8+ times
    back-to-back — the live-reproduced exact-phrase degeneration bug.
    Best-effort — never raises."""
    if not text:
        return False
    try:
        return bool(_REPETITION_DEGENERATION_RE.search(text))
    except Exception:  # noqa: BLE001
        return False


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


# ── Part B1 (tool-call observability) ──────────────────────────────
# `tool_calls`/`tool_sequence` above only ever kept NAMES — args and
# the actual result were discarded before persistence, so a claim
# like "verified via read_repo_file" could never be checked against
# what was ACTUALLY called (Part B2 needs this). Extends the SAME
# `task_chains` record with a parallel `tool_call_records` field
# carrying the full per-call shape orchestrator.py's `entry` dict
# already builds in-memory (name, args, ok, error, elapsed_ms,
# timestamp) — secret-redacted, still capped at 10 calls, still
# best-effort. Does not touch the existing fields at all.
def _tool_call_records(tool_calls: Optional[list], turn_id: str) -> list[dict]:
    out = []
    for tc in (tool_calls or [])[:10]:
        if isinstance(tc, str):
            out.append({"tool": tc, "turn_id": turn_id})
            continue
        tc = tc or {}
        args = tc.get("args") or {}
        out.append({
            "tool":       tc.get("tool") or tc.get("name") or "?",
            "args":       redact_secrets(_truncate(str(args), 300)),
            "ok":         tc.get("ok"),
            "error":      _truncate(tc.get("error"), 200) if tc.get("error") else None,
            "elapsed_ms": tc.get("elapsed_ms"),
            "timestamp":  tc.get("timestamp"),
            "turn_id":    turn_id,
        })
    return out


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
        # 2026-09-11 — see detect_output_repetition_degeneration's
        # docstring: a terminal-success turn whose actual text is a
        # known degenerated repeat-loop is reclassified as a failure
        # here so /admin/failure-patterns surfaces it for triage,
        # instead of it silently counting as a clean success.
        degenerated = success and detect_output_repetition_degeneration(final_response)
        if degenerated:
            success = False
        tool_seq = _tool_sequence(tool_calls)
        turn_id = uuid.uuid4().hex
        record = {
            "user_id":        user_id,
            "session_id":     session_id,
            "project_id":     project_id,
            "turn_id":        turn_id,
            "task_type":      classify_task_type(user_prompt),
            "user_prompt":    _truncate(redact_secrets(user_prompt or ""), 200),
            "model_plan":     _derive_model_plan(sequential_thoughts, tool_calls, final_response),
            "tool_calls":     tool_seq,
            "tool_sequence":  tool_seq,
            # Part B1 — full per-call trace (name, args, ok/error,
            # elapsed_ms, timestamp, turn_id) alongside the pre-
            # existing names-only fields above (unchanged, still used
            # by /admin/failure-patterns's group-by).
            "tool_call_records": _tool_call_records(tool_calls, turn_id),
            "final_response": _truncate(redact_secrets(final_response or ""), 500),
            "success":        success,
            "failure_reason": ("output_repetition_degeneration" if degenerated
                                else None if success
                                else (failure_mode or turn_state or "unknown")),
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


def get_turn_tool_records(tool_calls: Optional[list]) -> set[str]:
    """Part B2 helper — the set of tool NAMES actually invoked this
    turn, straight from the in-memory `tool_calls` list (same shape
    `_tool_sequence` reads). Used by `citation_guard.py::CitationGuard`
    to check a "verified via X" claim against what was REALLY called
    this turn, synchronously, before the response ships — no DB round
    trip, since this must block/regenerate before persistence."""
    return {n.lower() for n in _tool_sequence(tool_calls) if n and n != "?"}


async def log_unverified_tool_claims(
    db, *, user_id: str, session_id: str, project_id: Optional[str],
    claims: Optional[list],
) -> None:
    """Part B2 — persists each `unverified_tool_claims_blocked` entry
    CitationGuard flagged on `ctx` (it has no `db` handle that deep in
    the call stack) as a distinct, queryable event. Best-effort, same
    convention as `log_task_chain`/`log_architect_event` — never
    raises, logging must never break the turn it's observing."""
    if not claims or db is None:
        return
    try:
        for c in claims:
            await db.tool_claim_guard_events.insert_one({
                "event_type":    "unverified_tool_claim_blocked",
                "user_id":       user_id,
                "session_id":    session_id,
                "project_id":    project_id,
                "claimed_tools": c.get("claimed_tools") or [],
                "ts":            c.get("ts") or time.time(),
            })
    except Exception as e:  # noqa: BLE001
        logger.debug("log_unverified_tool_claims failed (non-blocking): %r", e)


__all__ = [
    "TASK_TYPES", "classify_task_type", "redact_secrets", "log_task_chain",
    "detect_output_repetition_degeneration", "get_turn_tool_records",
    "log_unverified_tool_claims",
]
