"""
services/task_state.py — 2026-02 Context-Confusion architecture fix,
Part A: explicit task-state object.

Ticket's framing: introduce a first-class `current_task_state` object
per conversation turn, written/read BEFORE any model call, so a short
confirmation message ("yes, go ahead") resolves against an explicit
`pending_action_ref` instead of the model re-deriving state from raw
chat history.

Investigation finding (read before writing anything, per this
engagement's standing rule): the DETERMINISTIC part of this — "a
confirm resolves against an explicit stored object, never an LLM
re-inference" — was ALREADY built and has been the DEFAULT-ON path
since an earlier round (`services/actions/pending_action.py`,
"2026-09-05 Commit-Boundary class fix"): `resolve_confirm()` runs
BEFORE tier classification / any LLM call, in its own dedicated
`pending_actions` Mongo collection with explicit status transitions
(PROPOSED -> AWAITING_CONFIRM -> EXECUTING -> VERIFIED), TTL, and
disambiguation for >1 pending action. That mechanism is NOT rebuilt
here — doing so would be exactly the "parallel/duplicate mechanism"
this engagement's standing rule forbids.

What THIS module adds: the explicit, uniformly-shaped
`current_task_state` VIEW the ticket asks for, as a thin read-only
projection over that existing state machine plus the turn's own
prompt — giving every OTHER part of the system (Part C's scope
enforcement, future observability/debugging, a future UI "what is
ORA working on right now" indicator) ONE canonical place to read
"what is the current task, and is anything pending" without needing
to know `pending_actions`' internal schema. This does not change
`pending_action.py`'s behavior at all — it is read-only.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional


async def get_current_task_state(
    db, *, session_id: str, user_id: str, prompt: str,
) -> dict:
    """Returns the schema the ticket specifies:
    {task_id, task_description, pending_action_ref, scope, created_at, updated_at}

    - `pending_action_ref`: the `id` of the session's single active
      AWAITING_CONFIRM action (its natural, already-existing ref), or
      None. When >1 action is active (the disambiguation case
      `pending_action.py` already handles at the prompt-resolution
      layer), this surfaces the MOST RECENT one — this projection is
      informational, not itself a resolver.
    - `task_description`: the CURRENT turn's prompt verbatim — the
      user's actual current request, exactly as Part A specifies
      ("the user's actual current request, verbatim"), not a
      re-summarized or model-inferred version.
    - `task_id`: stable across a proposal -> confirm pair (reuses the
      pending action's own `id` when one exists, so a task's
      lifecycle is traceable end-to-end under one id) — a fresh uuid
      otherwise (a genuinely new, not-yet-actionable request).
    - `scope`: `{files, allowed_operations}` — populated live during
      the CURRENT turn's tool-calling (`local_tools.py`'s
      `ctx["scope_files"]`, Part C), not persisted historically here;
      seeded with the target file already known from an active
      pending action's payload, if any (an edit/insert action already
      names its one file).
    """
    now = time.time()
    pending_ref: Optional[str] = None
    scope_files: list[str] = []
    task_id = uuid.uuid4().hex
    created_at = now

    if db is not None and session_id:
        try:
            from services.actions.pending_action import get_active_actions
            active = await get_active_actions(db, session_id=session_id, user_id=user_id)
            if active:
                action = active[-1]
                pending_ref = action.get("id")
                task_id = action.get("id") or task_id
                created_at = action.get("created_at") or now
                p = action.get("payload") or {}
                if p.get("path"):
                    scope_files.append(p["path"])
        except Exception:                                     # noqa: BLE001
            pass  # read-only projection — never break the real turn over this

    return {
        "task_id":            task_id,
        "task_description":   prompt or "",
        "pending_action_ref": pending_ref,
        "scope":              {"files": scope_files, "allowed_operations": []},
        "created_at":         created_at,
        "updated_at":         now,
    }
