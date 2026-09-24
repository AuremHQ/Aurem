"""
services/engagement_nudge.py — Mandatory Repo-Connect Gate + Post-
Connect Engagement Nudge (2026-09-09), Part 2.

`is_real_task_message` is the founder's ticket's exact, deliberately
simple rule: a chat message counts as a "real task" if it is NOT a
single word AND NOT one of a small ignore-list of greetings/acks
(case-insensitive, punctuation stripped). Anything else counts.

`maybe_fire_task_sent` is called best-effort from BOTH POST /chat/send
and POST /chat/stream the moment a user's prompt arrives — BEFORE the
orchestrator decides whether it becomes an actual `cto_tasks` row, a
plain LLM answer, or nothing. That's deliberate: the ticket measures
"did the user try to ask ORA for something" as its own signal,
independent of what the agent ends up doing with it downstream.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

_IGNORE_PHRASES = frozenset({"hi", "hello", "hey", "ok", "thanks", "cool"})

# A pending nudge stops being attributable to the next real task
# message after this long, so a message sent a week later can't get
# (mis)credited to a long-dismissed nudge. Not specified in the
# ticket ("a reasonable window") — 24h is a deliberate, documented
# default, not a silent guess.
TASK_SENT_ATTRIBUTION_WINDOW_S = 24 * 60 * 60


def is_real_task_message(text: Optional[str]) -> bool:
    normalized = re.sub(r"[^\w\s]", "", (text or "")).strip().lower()
    if not normalized:
        return False
    if normalized in _IGNORE_PHRASES:
        return False
    return len(normalized.split()) >= 2


async def maybe_fire_task_sent(db, *, user_id: str, prompt: str) -> None:
    """Best-effort, fire-and-forget. Never raises — a bug here must
    never break the real chat send/stream path."""
    if db is None or not user_id:
        return
    try:
        if not is_real_task_message(prompt):
            return
        now = time.time()
        user = await db.dev_users.find_one(
            {"user_id": user_id}, {"_id": 0, "pending_nudge": 1},
        )
        await db.dev_users.update_one(
            {"user_id": user_id},
            {"$set": {"last_real_task_message_at": now}},
        )
        pending = (user or {}).get("pending_nudge")
        if not pending or pending.get("fired"):
            return
        shown_at = pending.get("shown_at") or 0
        if now - shown_at > TASK_SENT_ATTRIBUTION_WINDOW_S:
            return
        from services.signup_guards import emit_engagement_nudge_task_sent
        await emit_engagement_nudge_task_sent(
            db, user_id=user_id, trigger=pending.get("trigger"),
            seconds_since_shown=round(now - shown_at),
        )
        await db.dev_users.update_one(
            {"user_id": user_id},
            {"$set": {"pending_nudge.fired": True}},
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("maybe_fire_task_sent failed: %r", e)
