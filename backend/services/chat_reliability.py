"""
services/chat_reliability.py — INTERACTION_CONTRACT_V1 P1 follow-up:
request-level durable ledger for chat turns, closing two named gaps
from `memory/CODING_AGENT_BLUEPRINT_V2.md`/ROADMAP.md:

1. "Provider-retry idempotency-key + capped-exponential-backoff layer
   for transient provider errors — today 'Retry' resends a fresh user
   turn, not an idempotent retry of the same provider call."
   A naive retry after a dropped connection/timeout mints a BRAND NEW
   request_id and re-runs the full worker (LLM + tool dispatch) from
   scratch. If the ORIGINAL request had already completed server-side
   (the client just never received/processed the response) and that
   completion included a side-effecting tool call, a naive retry
   duplicates that side effect — exactly the class of bug the
   reliability blueprint's Section 3 ("a timeout does not prove an
   operation failed") warns about.

2. "Full started/streaming/complete/cancelled/unknown stream-state
   enum persisted to Mongo — today only a boolean `interrupted` flag
   is persisted."

Design (deliberately small — this is NOT a general job queue):
- One Mongo collection, `chat_request_ledger`, one row per top-level
  chat turn (`/chat/stream` or `/chat/send`), keyed by the SAME
  `interaction_request_id` `services.interaction_contract.new_request_id()`
  already mints on every turn (unconditionally, flag-independent).
- `record_started()` is AWAITED (not fire-and-forget) before the
  worker/provider call begins — this is the "task accepted, not
  silently lost" durability point. If the process crashes between
  `record_started()` and any terminal `record_*()` call, the row is
  left at `status="started"` forever — a real, queryable, honest
  artifact proving the request was accepted (see
  `find_stale_started()`, used by the crash-survival demonstration).
  This module does NOT attempt automatic resume/replay of a crashed
  turn's own side effects (out of scope for a stateless HTTP+SSE chat
  turn with no persistent job queue) — it only guarantees the
  ACCEPTANCE record durably survives a crash, and that a genuinely
  safe replay (see `lookup_retry`) is possible for the one case where
  it matters most: the user clicking Retry after not getting a
  response.
- `lookup_retry()` is the actual idempotency check: a retry that
  supplies `retry_of_request_id` gets the ORIGINAL cached completed
  result replayed verbatim — zero new provider call, zero new tool
  dispatch — instead of a second, potentially side-effect-duplicating
  attempt. A still-`started` or `failed` original record returns None
  (no safe replay exists yet) — the caller proceeds with a genuinely
  fresh attempt, which is always safe for a `failed` record (nothing
  completed) and is the conservative, honest choice for a `started`
  record (Section 3: "a timeout does not prove an operation failed" —
  this app has no cross-request tool-effect journal to positively
  RECONCILE a still-ambiguous in-flight record against, so it does not
  fabricate a decision either way; it only avoids blindly re-running
  something already KNOWN to have finished).
- Gated behind `services.interaction_contract.enabled()` everywhere it
  is called from `routers/chat/*` — same single-flag rollout story as
  the rest of the reliability contract. Every function here is
  best-effort (never raises into the caller's real chat turn).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Past this many seconds with no terminal status, a `started` row is
# "stale" — i.e. the original attempt almost certainly crashed or was
# abandoned rather than still genuinely in-flight. Used ONLY for the
# read-only `find_stale_started()` diagnostic below, never to silently
# auto-retry (see docstring: an ambiguous outcome is never guessed at).
STALE_STARTED_S = 240


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def record_started(db, *, request_id: str, session_id: str, user_id: str,
                          project_id: Optional[str], prompt: str, route: str) -> None:
    """Durably record that this turn was ACCEPTED, before any
    provider/tool call runs. Awaited by the caller — this is the
    line between "the platform lost the task silently" and "the
    platform has proof it accepted the task", independent of whether
    the turn itself later succeeds, fails, or the process crashes."""
    if db is None or not request_id:
        return
    try:
        await db.chat_request_ledger.update_one(
            {"request_id": request_id},
            {"$setOnInsert": {
                "request_id": request_id, "session_id": session_id or "",
                "user_id": user_id, "project_id": project_id,
                "prompt_hash": _hash(prompt), "route": route,
                "status": "started", "stream_state": "started",
                "created_at": _now_iso(),
            }},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("chat_reliability.record_started skipped: %r", e)


async def record_completed(db, *, request_id: str, content: str, provider: str,
                            tool_calls_run: int, turn_state: str, scope_confirmation=None) -> None:
    """Terminal state: the turn produced a real, delivered result.
    This is the ONLY status `lookup_retry()` will ever replay from —
    a completed record is the one case where re-running the provider/
    tools would be pure duplication, not recovery."""
    if db is None or not request_id:
        return
    try:
        await db.chat_request_ledger.update_one(
            {"request_id": request_id},
            {"$set": {
                "status": "completed", "stream_state": "complete",
                "content": content, "provider": provider,
                "tool_calls_run": int(tool_calls_run or 0),
                "turn_state": turn_state or "success",
                "completed_at": _now_iso(),
                **({"scope_confirmation": scope_confirmation} if scope_confirmation else {}),
            }},
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("chat_reliability.record_completed skipped: %r", e)


async def record_cancelled(db, *, request_id: str, reason: str,
                            partial_content: str = "", tool_calls_run: int = 0) -> None:
    """Terminal state: WE (the server) tore the turn down deliberately
    — either our own timeout guard, or reacting to a mid-stream client
    disconnect. Distinct from `unknown` because the outcome IS known:
    no further provider/tool work happened after this point."""
    if db is None or not request_id:
        return
    try:
        await db.chat_request_ledger.update_one(
            {"request_id": request_id},
            {"$set": {
                "status": "cancelled", "stream_state": "cancelled",
                "reason": reason, "content": partial_content,
                "tool_calls_run": int(tool_calls_run or 0),
                "completed_at": _now_iso(),
            }},
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("chat_reliability.record_cancelled skipped: %r", e)


async def record_unknown(db, *, request_id: str, reason: str) -> None:
    """Terminal-for-this-attempt state: the worker raised mid-flight
    with no definitive content — we cannot positively confirm whether
    a tool side effect completed before the exception. Never silently
    relabeled as `completed` or `cancelled` — an honest `unknown` is
    what `lookup_retry()` refuses to replay from (see module
    docstring)."""
    if db is None or not request_id:
        return
    try:
        await db.chat_request_ledger.update_one(
            {"request_id": request_id},
            {"$set": {
                "status": "unknown", "stream_state": "unknown",
                "reason": reason, "completed_at": _now_iso(),
            }},
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("chat_reliability.record_unknown skipped: %r", e)


async def lookup_retry(db, *, retry_of_request_id: str, session_id: str,
                        user_id: str) -> Optional[dict]:
    """Returns the cached `completed` record if a safe replay exists,
    else None (caller proceeds with a genuinely fresh attempt — always
    safe when the original was never `completed`)."""
    if db is None or not retry_of_request_id:
        return None
    try:
        rec = await db.chat_request_ledger.find_one({
            "request_id": retry_of_request_id,
            "session_id": session_id or "",
            "user_id": user_id,
        })
    except Exception as e:  # noqa: BLE001
        logger.debug("chat_reliability.lookup_retry skipped: %r", e)
        return None
    # Only a genuinely successful/delivered original is safe to replay
    # verbatim. A `completed` record whose `turn_state` is "error" means
    # the transport finished but the CONTENT was a failure — replaying
    # that same failure would deny the user a real fresh attempt, which
    # is exactly what "Retry" is supposed to give them when nothing
    # succeeded the first time. Only "success"/"partial" are replayed.
    if rec and rec.get("status") == "completed" and rec.get("turn_state") != "error":
        return rec
    return None


async def find_stale_started(db, *, older_than_s: int = STALE_STARTED_S, limit: int = 50) -> list:
    """Read-only diagnostic (not auto-acted-on): rows still `started`
    past `older_than_s` — the durable evidence trail for "did the
    platform lose an accepted task silently" audits / the crash-
    survival demonstration. Never mutates anything."""
    if db is None:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_s)).isoformat()
    try:
        cur = db.chat_request_ledger.find(
            {"status": "started", "created_at": {"$lt": cutoff}},
        ).sort("created_at", -1).limit(limit)
        return [row async for row in cur]
    except Exception as e:  # noqa: BLE001
        logger.debug("chat_reliability.find_stale_started skipped: %r", e)
        return []


# 2026-09-22 — reconciler cron (testing_agent-flagged gap: a real client
# TCP disconnect while a turn is still waiting on the worker does NOT
# reliably raise CancelledError/GeneratorExit into `gen()` in this
# environment's uvicorn/ASGI stack — verified via live repro: killed
# curl mid-request, `record_cancelled()`'s except-clause never ran,
# row stayed at `started` indefinitely even past the 180s hard
# deadline). Section 3 of the reliability blueprint: "reconcile
# interrupted jobs after restart" — this is that reconciliation, run
# continuously rather than only at restart, since the failure mode
# here is a live-but-abandoned request, not just a crashed process.
# Deliberately marks `unknown`, NEVER `cancelled` or `completed` —
# we truly do not know whether the worker is still running server-
# side or already died; only that it has been silent for too long to
# still trust as a normal in-flight request. `lookup_retry()` already
# refuses to replay anything but a `completed` record, so this is
# safe regardless of which is actually true.
RECONCILER_INTERVAL_S = 60


async def chat_ledger_reconciler_loop() -> None:
    """Long-lived background loop (registered via `_supervise(...,
    long_lived=True)` in main.py, same convention as
    services/health_notifier.py / services/journey_watch.py)."""
    from cto_services.db import get_db
    while True:
        try:
            await asyncio.sleep(RECONCILER_INTERVAL_S)
            db = get_db()
            stale = await find_stale_started(db, older_than_s=STALE_STARTED_S)
            for row in stale:
                try:
                    await db.chat_request_ledger.update_one(
                        {"request_id": row["request_id"], "status": "started"},
                        {"$set": {
                            "status": "unknown", "stream_state": "unknown",
                            "reason": "stale_started_reconciled_by_sweeper",
                            "completed_at": _now_iso(),
                        }},
                    )
                    logger.warning(
                        "chat_reliability reconciler: request_id=%s stuck at "
                        "'started' for >%ss — marked 'unknown' (client almost "
                        "certainly disconnected without the server ever "
                        "detecting it; see stream.py's CancelledError handler)",
                        row.get("request_id"), STALE_STARTED_S,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("reconciler row update skipped: %r", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("chat_ledger_reconciler_loop tick skipped: %r", e)


def _hash(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode()).hexdigest()
