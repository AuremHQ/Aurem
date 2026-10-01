"""
services/commit_boundary.py — 2026-09-05 Commit-Boundary class fix
(integration glue).

ONE call site each in routers/chat.py for /chat/send and
/chat/stream. Gated by `COMMIT_BOUNDARY_ENABLED` (backend/.env,
default "true"):

  ON  (default) — routes to the new deterministic PendingAction
       machine (services/actions/pending_action.py). A confirmation
       is resolved as a server-side state transition, never a model
       turn.
  OFF — falls back verbatim to the prior (2026-09-04)
       services/confirm_execution.py guard-based behavior — founder's
       explicit rollback switch, no code change needed to flip back
       if the new mechanism needs to be pulled.
"""
from __future__ import annotations

import os
import re
from typing import Optional


def commit_boundary_enabled() -> bool:
    v = (os.environ.get("COMMIT_BOUNDARY_ENABLED") or "true").strip().lower()
    return v not in ("false", "0", "off", "no")


async def resolve_turn_start(
    db, *, user: dict, session_id: str, project_id: Optional[str],
    prompt: str, bin_ctx,
) -> Optional[dict]:
    """Checked BEFORE tier classification / any LLM call. Returns a
    chat-result-shaped dict when a confirm/cancel intent was resolved
    deterministically; returns None to let the caller fall through to
    normal routing (a genuine new request)."""
    if commit_boundary_enabled():
        from services.actions.pending_action import resolve_confirm
        return await resolve_confirm(
            db, session_id=session_id, user_id=(user or {}).get("user_id"),
            project_id=project_id, prompt=prompt, user=user, bin_ctx=bin_ctx,
        )
    from services.confirm_execution import maybe_execute_pending
    return await maybe_execute_pending(
        db, user=user, session_id=session_id, project_id=project_id,
        prompt=prompt, bin_ctx=bin_ctx,
    )


async def propose_from_turn(
    db, *, session_id: str, user_id: str, project_id: Optional[str],
    provider: str, assistant_reply: str, bin_ctx=None, user_prompt: str = "",
) -> None:
    """Called once per real turn at persist-time — the ONLY place a
    confirmable action can be created."""
    if commit_boundary_enabled():
        from services.actions.pending_action import propose_from_turn as _propose
        await _propose(
            db, session_id=session_id, user_id=user_id, project_id=project_id,
            provider=provider, assistant_reply=assistant_reply, bin_ctx=bin_ctx,
            user_prompt=user_prompt,
        )
        return
    from services.confirm_execution import (
        register_code_fence_pending, register_upgrade_pending, clear_pending_action,
    )
    if "```aurem-handoff" in (assistant_reply or ""):
        _brief_m = re.search(r"```aurem-handoff\s*\n(.*?)```", assistant_reply or "", re.DOTALL)
        await register_code_fence_pending(
            db, session_id=session_id, project_id=project_id,
            proposal_text=assistant_reply,
            brief=(_brief_m.group(1).strip() if _brief_m else ""),
        )
    elif provider == "edit-tier-upgrade-offer":
        await register_upgrade_pending(db, session_id=session_id, plan="pro")
    elif provider != "confirm-executor":
        await clear_pending_action(db, session_id=session_id)


# ── requirement #9: never let the model promise "ready when you
# approve"/"say go" as a SEPARATE trailing question after a real
# aurem-handoff fence. The fence itself IS the ask (Approve button);
# a dangling permission question after it is always misleading under
# this architecture (whether or not this turn's action was
# concretized into anything text-confirmable) and slips through the
# persona's own "no permission-asking" rule occasionally in practice.
_FENCE_RE = re.compile(r"```aurem-handoff.*?```", re.DOTALL)
_TRAILING_PROMISE_RE = re.compile(
    r"(ready when you|shall i|should i|let me know|want me to|"
    r"say\s+go|reply\s+(?:yes|go|approve)|approve\s+this|confirm\s+this)",
    re.IGNORECASE,
)


# 2026-02 — confirmed live regression (founder QA report): the
# App.js "+2/-58" incident (2026-09-13, `_dropped_import_reasons()`
# etc. in cto_projects_helpers.py) was fixed ONLY inside
# worker_api.py/cto_pipeline_steps.py's task-execution pipeline — the
# code that runs AFTER the user clicks "Approve the fix". That fix
# never covered THIS surface: the system prompt tells the model the
# ```aurem-handoff fence itself must be a bare one-paragraph brief
# with "No code blocks inside — the worker writes the code itself
# based on this brief" (services/orchestrator.py) — but nothing stops
# the model from writing a full proposed code block SOMEWHERE ELSE in
# the SAME reply, alongside the fence. The frontend's Cursor-like
# diff-peek feature (`extractShipFiles`/`FileDiffPeek` in
# MessageBubble.jsx) is specifically designed to pick up exactly such
# a block, match it to a file path named in the brief, fetch the real
# GitHub content, and render a live side-by-side diff — entirely
# client-side, with ZERO safety gating (no `_dropped_import_reasons`,
# no hallucination check — those only exist in the POST-approval
# worker pipeline). Confirmed repro (index.js, +2/-47, dropped all 4
# top-level imports): the model wrote an off-spec illustrative code
# block next to the brief, and the diff-peek rendered it as a "plan
# clean ✓" preview with zero verification, before Approve was ever
# clicked — an entirely different, ungated code path than the one the
# earlier fix covered, not a narrower version of the same fix.
#
# Fix: per the system prompt's own stated contract, a well-formed
# ```aurem-handoff reply should NEVER contain any other fenced code
# block. Rather than trying to replicate cto_projects_helpers.py's
# import-safety heuristics against the frontend's own loose path-
# matching logic (fragile, two implementations to keep in sync),
# enforce the EXISTING contract deterministically: strip every fenced
# block that is not the handoff fence itself before the reply ever
# reaches the user. `extractShipFiles` then finds no code block for
# any path, `peekFiles.length === 0`, and the diff-peek row correctly
# renders nothing — no misleading, ungated preview — instead of a
# scary, unverified diff next to a "clean ✓" badge.
#
# Implementation note: a naive "any ``` not immediately followed by
# aurem-handoff" regex is unsafe here — the handoff fence's own
# CLOSING ``` also isn't followed by "aurem-handoff" (that string
# only ever appears once, right after the OPENING ```), so a single
# combined pattern matches from that closing marker forward into the
# NEXT real code block and merges the two into one garbled block.
# Same fix shape as business_voice_filter.py's URL protection: pull
# the handoff fence out FIRST (its span is unambiguous — `_FENCE_RE`
# already matches it as one atomic unit), strip every remaining ```
# block from what's left, then put the handoff fence back verbatim.
_ANY_FENCE_RE = re.compile(r"```[^\n`]*\n[\s\S]*?```")


def strip_stray_code_blocks_near_handoff(content: str) -> str:
    if not content or "```aurem-handoff" not in content:
        return content
    handoff_match = _FENCE_RE.search(content)
    if not handoff_match:
        return content
    before, handoff, after = (
        content[:handoff_match.start()], handoff_match.group(0), content[handoff_match.end():],
    )
    before = _ANY_FENCE_RE.sub("", before)
    after = _ANY_FENCE_RE.sub("", after)
    out = f"{before}\n{handoff}\n{after}" if (before.strip() or after.strip()) else handoff
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def strip_false_confirm_promise(content: str) -> str:
    if not content or "```aurem-handoff" not in content:
        return content
    matches = list(_FENCE_RE.finditer(content))
    if not matches:
        return content
    last_end = matches[-1].end()
    trailing = content[last_end:]
    if not trailing.strip():
        return content
    if "?" in trailing and _TRAILING_PROMISE_RE.search(trailing):
        return content[:last_end].rstrip()
    return content
