"""
services/sequential_thinking_skills.py — Sequential Thinking tool.
Master Plan "BUILD QUALITY" · Phase S2.

WHY: on a genuinely complex, multi-file / multi-step debugging task,
an LLM that writes its fix in one shot tends to patch the FIRST
plausible cause it sees instead of the ROOT cause. This tool gives
the model an explicit scratchpad — record a numbered thought, mark
whether another thought is needed, optionally revise or branch a
prior thought — before it commits to a fix. Same shape as the
reference "sequential-thinking" MCP server (record/revise/branch
numbered thought steps); no external network call, no credentials —
this is a LOCAL tool, purely structural (confirmed via the
integration playbook: sequential thinking is a self-contained
reasoning scaffold, not a third-party API).

Gate: `sequential_thinking` is added to the tool catalog ONLY when the
user's message carries multi-file / multi-step / whole-system
debugging signals ("debug this system", "across multiple files",
"root cause", "intermittent", etc). A trivial one-line ask ("change
the button color to blue") never sees this tool in its catalog —
saves the schema tokens on every turn that doesn't need it. This
module's `sequential_thinking` function itself has NO opinion on
when it's offered; the gate lives entirely in the tool-routing
module so the whole on/off decision is one grep-able, unit-testable
place.

State: kept on `ctx["sequential_thoughts"]` — a plain list appended
to in-place, scoped to the single chat turn's `ctx` dict (same
lifet, same conventions as `ctx["tool_calls"]` in local_tools.py).
Never persisted across turns/requests.

Cost ledger: every call writes a lightweight row to
`sequential_thinking_calls` (thought_number, total_thoughts, is_revision,
branch_id, cost_usd 0.0 — it's a local tool, not billed per-call) —
same convention as `context7_skills.log_context7_call`.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


async def log_sequential_thinking_call(
    *, thought_number: int, total_thoughts: int, is_revision: bool,
    branch_id: Optional[str], next_thought_needed: bool,
) -> None:
    """Best-effort row in `sequential_thinking_calls`. Never raises —
    logging must never break the tool it's logging."""
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None:
            return
        await db.sequential_thinking_calls.insert_one({
            "thought_number":       int(thought_number),
            "total_thoughts":       int(total_thoughts),
            "is_revision":          bool(is_revision),
            "branch_id":            branch_id,
            "next_thought_needed":  bool(next_thought_needed),
            "cost_usd":             0.0,  # local tool — no separate billing
            "ts":                   time.time(),
        })
    except Exception as e:  # noqa: BLE001
        logger.debug("log_sequential_thinking_call failed (non-blocking): %r", e)


async def sequential_thinking(ctx: dict, args: dict) -> dict:
    """Record one numbered step of step-by-step reasoning before
    committing to a fix. See SEQUENTIAL_THINKING_TOOL_SPECS below for
    the LLM-facing description of WHEN/HOW to call this."""
    args = args or {}
    thought = (args.get("thought") or "").strip()
    if not thought:
        return {"ok": False, "error": "Missing required arg `thought`"}

    try:
        thought_number = int(args.get("thought_number") or 1)
    except (TypeError, ValueError):
        return {"ok": False, "error": "`thought_number` must be an int"}
    try:
        total_thoughts = int(args.get("total_thoughts") or thought_number)
    except (TypeError, ValueError):
        return {"ok": False, "error": "`total_thoughts` must be an int"}
    next_thought_needed = bool(args.get("next_thought_needed", False))
    is_revision = bool(args.get("is_revision", False))
    revises_thought = args.get("revises_thought")
    branch_from_thought = args.get("branch_from_thought")
    branch_id = args.get("branch_id")
    needs_more_thoughts = bool(args.get("needs_more_thoughts", False))

    history = ctx.setdefault("sequential_thoughts", [])
    history.append({
        "thought_number":       thought_number,
        "thought":              thought,
        "is_revision":          is_revision,
        "revises_thought":      revises_thought,
        "branch_from_thought":  branch_from_thought,
        "branch_id":            branch_id,
    })

    await log_sequential_thinking_call(
        thought_number=thought_number, total_thoughts=total_thoughts,
        is_revision=is_revision, branch_id=branch_id,
        next_thought_needed=next_thought_needed,
    )

    return {
        "ok": True,
        "thought_number":       thought_number,
        "total_thoughts":       max(total_thoughts, thought_number),
        "next_thought_needed":  next_thought_needed,
        "needs_more_thoughts":  needs_more_thoughts,
        "thought_history_length": len(history),
        "branches": sorted({h["branch_id"] for h in history if h.get("branch_id")}),
    }


SEQUENTIAL_THINKING_TOOLS = {"sequential_thinking": sequential_thinking}


SEQUENTIAL_THINKING_TOOL_SPECS = [
    {
        "name": "sequential_thinking",
        "description": (
            "Scratchpad for step-by-step reasoning on a COMPLEX, multi-file "
            "or multi-step debugging task — record one numbered thought at "
            "a time before committing to a fix, so you investigate the ROOT "
            "cause across the files involved instead of patching the first "
            "plausible one. Set next_thought_needed=true to keep going, "
            "false once you're ready to write the fix. You can revise a "
            "prior thought (is_revision, revises_thought) or branch an "
            "alternate line of investigation (branch_from_thought, "
            "branch_id). This tool only appears in your toolbox for "
            "requests that are actually complex — do not force extra "
            "thought steps on a simple, one-file change."
        ),
        "args_spec": {
            "thought":              "string — this step's reasoning",
            "thought_number":       "int — 1-indexed position in the sequence",
            "total_thoughts":       "int — current estimate of how many thoughts you'll need",
            "next_thought_needed":  "bool — true to keep reasoning, false when ready to answer",
            "is_revision":          "optional bool — true if this revises an earlier thought",
            "revises_thought":      "optional int — which thought_number is being revised",
            "branch_from_thought":  "optional int — thought_number this branch diverges from",
            "branch_id":            "optional string — label for this branch of reasoning",
            "needs_more_thoughts":  "optional bool — true if total_thoughts turned out too low",
        },
    },
]


__all__ = [
    "SEQUENTIAL_THINKING_TOOLS", "SEQUENTIAL_THINKING_TOOL_SPECS",
    "sequential_thinking", "log_sequential_thinking_call",
]
