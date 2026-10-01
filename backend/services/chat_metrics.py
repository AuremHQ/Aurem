"""
services/chat_metrics.py — daily counters for chat turn quality.

One document per UTC day in `chat_metrics`, fields incremented with $inc:
  turns.<OUTCOME>            every finished turn by outcome
  reasons.<reason>           NOTHING_DONE/other reasons (answered, announced_not_done...)
  tool_failures              tool calls that came back not ok
  tool_retries               read-only calls retried once
  state_answers              turns answered from stored records
Pending changes created vs approved are read from `pending_actions`
(`summary`), not double-counted here. Fire-and-forget: a metrics write
never blocks or fails a chat turn.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def counters_for_turn(result: dict, interaction: dict | None) -> dict:
    inc: dict[str, int] = {}
    if interaction and interaction.get("outcome"):
        inc[f"turns.{interaction['outcome']}"] = 1
        inc[f"reasons.{interaction.get('reason') or 'none'}"] = 1
    invocations = [i for i in (result.get("tool_invocations") or []) if isinstance(i, dict)]
    failed = sum(1 for i in invocations if i.get("ok") is False)
    retried = sum(1 for i in invocations if i.get("retried"))
    if failed:
        inc["tool_failures"] = failed
    if retried:
        inc["tool_retries"] = retried
    if result.get("provider") == "state-answer":
        inc["state_answers"] = 1
    return inc


async def record_turn(db, result: dict, interaction: dict | None) -> None:
    try:
        inc = counters_for_turn(result, interaction)
        if db is not None and inc:
            await db.chat_metrics.update_one(
                {"day": _day()}, {"$inc": inc}, upsert=True)
    except Exception as e:                                   # noqa: BLE001
        logger.debug("chat_metrics write skipped: %r", e)


async def summary(db, days: int = 7) -> dict:
    since = _day(datetime.now(timezone.utc) - timedelta(days=max(1, days) - 1))
    totals: dict = {}
    async for doc in db.chat_metrics.find({"day": {"$gte": since}}, {"_id": 0}):
        for key, value in _flatten(doc).items():
            totals[key] = totals.get(key, 0) + value
    pending = {}
    for status in ("AWAITING_CONFIRM", "VERIFIED", "APPLIED_FAILED", "CANCELLED"):
        pending[status] = await db.pending_actions.count_documents({"status": status})
    return {"since": since, "counters": totals, "pending_actions_by_status": pending}


def _flatten(doc: dict, prefix: str = "") -> dict:
    flat = {}
    for key, value in doc.items():
        if key == "day":
            continue
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        elif isinstance(value, (int, float)):
            flat[name] = value
    return flat
