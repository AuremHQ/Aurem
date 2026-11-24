"""
routers/admin_health.py — Unified Health aggregator (Feb 2026)

Two endpoints backing the admin cockpit + notification bell + every
per-page health tile:

  GET  /admin/status/all           run every registered check_fn
  POST /admin/status/{id}/ack      acknowledge-and-mute a check

Aggregation contract (from services/health_registry.py):
  { checks: [
      { id, name, category, status: green|red|gray,
        detail, checked_at, red_since, acked_until },
    ],
    generated_at, took_ms, counts: {green, red, gray, total} }

TTL cache (5-10s, env-configurable) is deliberately short — the
whole point of the registry is LIVE data. Cache exists only so that
30 admin sessions polling every 30s don't hammer real infra 900
times a minute.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends

from cto_services.auth import require_admin_dep
from services.health_registry import all_checks, get_check, run_check_safely

# Import triggers registration of every adapter.
import services.health_checks  # noqa: F401  — side-effect registration


def __import_env_stamp():
    """Lazy import so the helper is picked up whenever it's called
    without introducing a hard load-order dependency."""
    from services.env_context import env_stamp
    return env_stamp()

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/aurem-dev/admin/status",
    tags=["Admin-health"],
    dependencies=[Depends(require_admin_dep)],
)


# ── Cache ──────────────────────────────────────────────────────────

_STATUS_CACHE: dict = {"payload": None, "expires_at": 0.0}
_STATUS_CACHE_TTL_S = int(os.environ.get("HEALTH_STATUS_CACHE_TTL_S", "8"))


# ── Ack + red_since state (in Mongo so it survives restarts) ───────
#
# We deliberately keep the "when did this check first go red" and
# "acked-until" fields in the DB rather than in-memory. Founder
# acking a red at 3am must survive the next uvicorn worker cycle.

async def _get_state_collection():
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return None
    return db.health_check_state


async def _load_state(check_id: str) -> dict:
    """Return {red_since: iso|None, acked_until: iso|None}."""
    coll = await _get_state_collection()
    if coll is None:
        return {"red_since": None, "acked_until": None}
    row = await coll.find_one({"_id": check_id})
    return {
        "red_since":   (row or {}).get("red_since"),
        "acked_until": (row or {}).get("acked_until"),
    }


async def _persist_red_since(check_id: str, when: str) -> None:
    coll = await _get_state_collection()
    if coll is None:
        return
    await coll.update_one(
        {"_id": check_id},
        {"$set": {"red_since": when}},
        upsert=True,
    )


async def _clear_red_since(check_id: str) -> None:
    coll = await _get_state_collection()
    if coll is None:
        return
    await coll.update_one(
        {"_id": check_id},
        {"$set": {"red_since": None}},
    )


async def _persist_ack(check_id: str, until_iso: Optional[str]) -> None:
    coll = await _get_state_collection()
    if coll is None:
        raise HTTPException(503, "database unavailable — cannot persist ack")
    await coll.update_one(
        {"_id": check_id},
        {"$set": {"acked_until": until_iso}},
        upsert=True,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ack_active(acked_until: Optional[str]) -> bool:
    if not acked_until:
        return False
    try:
        return datetime.fromisoformat(acked_until.replace("Z", "+00:00")) \
             > datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001 — corrupt row treated as unacked
        return False


# ── Endpoints ──────────────────────────────────────────────────────

# ---------------------------------------------------------------------------
# Non-blocking aggregator (Nov 2026 prod incident, 2026-11-21)
# ---------------------------------------------------------------------------
# A check_fn that does synchronous I/O (time.sleep, requests, a hung
# socket, ...) blocks the request event loop, and asyncio.wait_for
# cannot cancel it -- there is no yield point for the cancellation to
# land on. The 20s outer aggregator timeout could therefore only fire
# after the block ended, and while the loop was stuck EVERY request
# stalled, including /health, so the platform reported the backend
# down (28 nginx upstream timeouts on GET /health in one minute).
#
# Checks run on a small fixed pool of persistent background event
# loops, each in its own daemon thread -- never the request loop --
# with a per-check timeout enforced from the request loop. A check
# that hangs degrades to its own red result and the endpoint still
# returns promptly. Checks are spread round-robin across the pool so
# one check blocking its own loop (e.g. real sync I/O) cannot delay a
# DIFFERENT check that landed on a different loop.
#
# 2026-11-24 fix -- the first version of this (per-check `asyncio.run()`
# in a fresh thread-pool thread) created a BRAND NEW event loop for
# every single check, every single call. Motor's AsyncIOMotorClient
# (the shared cto_services.db handle) binds internal asyncio.Lock /
# Future objects to whichever loop first used it; reusing that same
# client from a different loop crashed with "Task ... got Future ...
# attached to a different loop" -- the exact error every DB-touching
# guard (G1, G5, G10, G15, G20, infra_db) was showing in the cockpit.
# A small FIXED pool of long-lived loops (not one new loop per check
# per call) keeps the request loop free, keeps one check's blocking
# I/O isolated from the rest, AND gives cto_services.db a stable,
# bounded set of loops to bind its (few, reused) secondary clients to
# (see db.py) instead of spinning up a fresh client every call.
_CHECK_TIMEOUT_S = 8.0  # matches run_check_safely's per-check timeout
_BG_POOL_SIZE = 8
_bg_loops: list = [None] * _BG_POOL_SIZE
_bg_loop_locks = [threading.Lock() for _ in range(_BG_POOL_SIZE)]
_bg_rr_counter = 0
_bg_rr_lock = threading.Lock()


def _ensure_bg_loop(idx: int) -> asyncio.AbstractEventLoop:
    """Lazily start persistent background loop #idx, in its own
    daemon thread, the first time a check needs to run on it."""
    loop = _bg_loops[idx]
    if loop is not None and loop.is_running():
        return loop
    with _bg_loop_locks[idx]:
        loop = _bg_loops[idx]
        if loop is not None and loop.is_running():
            return loop
        ready = threading.Event()
        holder: dict = {}

        def _runner():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            holder["loop"] = new_loop
            ready.set()
            new_loop.run_forever()

        threading.Thread(
            target=_runner, name=f"admin-health-bg-loop-{idx}", daemon=True,
        ).start()
        ready.wait(timeout=5)
        _bg_loops[idx] = holder["loop"]
        return _bg_loops[idx]


def _next_bg_loop() -> asyncio.AbstractEventLoop:
    """Round-robin across the fixed pool so concurrent checks spread
    across different loops/threads instead of piling onto one."""
    global _bg_rr_counter
    with _bg_rr_lock:
        idx = _bg_rr_counter % _BG_POOL_SIZE
        _bg_rr_counter += 1
    return _ensure_bg_loop(idx)


async def _run_check_threaded(check):
    """Run one check on a pooled background loop, with a per-check
    timeout enforced from the request loop.

    Returns the check's result dict (with ``check_ms`` added), exactly
    as run_check_safely would return it. On timeout the check degrades
    to red -- the endpoint never hangs on a single check.
    """
    t0 = time.time()
    try:
        bg_loop = _next_bg_loop()
        future = asyncio.run_coroutine_threadsafe(run_check_safely(check), bg_loop)
        result = await asyncio.wait_for(
            asyncio.wrap_future(future), timeout=_CHECK_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error(
            "[status/all] check %s timed out after %ss -- degrading to red; "
            "the event loop was not blocked",
            check.id,
            _CHECK_TIMEOUT_S,
        )
        result = {
            "status": "red",
            "detail": f"check timed out after {_CHECK_TIMEOUT_S:g}s "
            "(blocking check ran off the event loop)",
            "checked_at": _iso_now(),
        }
    except Exception as e:  # noqa: BLE001 — background loop/submission failure
        logger.error("[status/all] check %s crashed: %s", check.id, e)
        result = {
            "status": "red",
            "detail": f"check crashed: {e}",
            "checked_at": _iso_now(),
        }
    result["check_ms"] = int((time.time() - t0) * 1000)
    return result


@router.get("/all")
async def status_all():
    """Run every registered check_fn in parallel, return the
    combined status snapshot. Short-TTL cached (default 8s).

    Feb 2026 · prod-hang fix — the individual `run_check_safely`
    wrapper already carries an 8s asyncio.wait_for guard, but that
    guard can't cancel a check_fn that blocks the event loop with
    sync I/O (asyncio.wait_for needs a yield point).  Defence-in-
    depth: wrap the whole aggregator in a 20s outer timeout so a
    misbehaving check can never leave the client hanging forever,
    AND record per-check wall-time so future regressions surface
    immediately in the response detail.

    Nov 2026 prod-hang fix -- the 20s outer timeout could only fire
    after a blocking check released the loop, so while it was stuck
    EVERY request stalled, including /health (2026-11-21). Each check
    now runs in the thread pool via _run_check_threaded (own event
    loop per check, per-check timeout enforced from the request
    loop), so a blocking check degrades to its own red result and
    the event loop -- and /health -- stay responsive. The 20s outer
    timeout remains as defence-in-depth."""
    now = time.time()
    if _STATUS_CACHE["payload"] and now < _STATUS_CACHE["expires_at"]:
        return _STATUS_CACHE["payload"]

    t0 = time.time()
    checks = all_checks()

    # Each check runs in the thread pool (_run_check_threaded): the
    # request event loop stays free even if a check blocks on sync I/O.

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[_run_check_threaded(c) for c in checks],
                           return_exceptions=False),
            timeout=20.0,
        )
    except asyncio.TimeoutError:
        # Aggregator itself timed out — one or more check_fns are
        # blocking the event loop.  Return a red overall snapshot so
        # the UI can render immediately instead of spinning forever.
        logger.error("[status/all] AGGREGATOR TIMED OUT after 20s — "
                     "one or more check_fns are blocking the event loop")
        return {
            "generated_at": _iso_now(),
            "took_ms":      int((time.time() - t0) * 1000),
            "counts": {"green": 0, "red": len(checks), "gray": 0,
                       "total": len(checks), "health_pct": 0.0},
            "checks": [{
                "id": c.id, "name": c.name, "category": c.category,
                "status": "red", "detail": "aggregator timed out",
                "checked_at": _iso_now(), "red_since": None,
                "acked_until": None, "ack_active": False,
                "check_ms": 20000,
            } for c in checks],
            **__import_env_stamp(),
            "aggregator_timeout": True,
        }

    rows: list[dict] = []
    now_iso = _iso_now()
    for check, res in zip(checks, results):
        state = await _load_state(check.id)
        status = res["status"]
        red_since = state["red_since"]
        # Roll red_since forward: first time we see red → stamp it;
        # green/gray transitions clear it.
        if status == "red" and not red_since:
            red_since = now_iso
            await _persist_red_since(check.id, red_since)
        elif status != "red" and red_since:
            red_since = None
            await _clear_red_since(check.id)

        rows.append({
            "id":         check.id,
            "name":       check.name,
            "category":   check.category,
            "status":     status,
            "detail":     res.get("detail", ""),
            "checked_at": res.get("checked_at"),
            "check_ms":   res.get("check_ms"),
            "red_since":  red_since,
            "acked_until": state["acked_until"],
            "ack_active":  _ack_active(state["acked_until"]),
        })

    counts = {
        "green": sum(1 for r in rows if r["status"] == "green"),
        "red":   sum(1 for r in rows if r["status"] == "red"),
        "gray":  sum(1 for r in rows if r["status"] == "gray"),
        "total": len(rows),
    }
    # Health % excludes gray (per 3-state discipline: gray is
    # "not-set-up-yet" — neither passing nor failing).
    denom = counts["green"] + counts["red"]
    counts["health_pct"] = round(100.0 * counts["green"] / denom, 1) if denom else None

    payload = {
        "generated_at": now_iso,
        "took_ms":      int((time.time() - t0) * 1000),
        "counts":       counts,
        "checks":       rows,
        # Env identification — same helper as /admin/pulse. Prevents
        # the "is this preview or prod data?" confusion the cockpit
        # audit surfaced in Feb 2026.
        **__import_env_stamp(),
    }
    _STATUS_CACHE["payload"]    = payload
    _STATUS_CACHE["expires_at"] = now + _STATUS_CACHE_TTL_S
    return payload


@router.post("/{check_id}/ack")
async def ack_check(check_id: str, until: Optional[str] = None):
    """Acknowledge-and-mute a check until an ISO timestamp.

    Body/query param `until` (ISO-8601). Pass empty/null to clear
    a prior ack. Acked checks still appear in /all but with
    `ack_active: true`; they're excluded from the notification-bell
    badge and the cockpit "Needs Attention" list.
    """
    if not get_check(check_id):
        raise HTTPException(404, f"unknown check id: {check_id}")
    if until:
        try:
            datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "until must be ISO-8601 UTC")
    await _persist_ack(check_id, until or None)
    # Invalidate the aggregator cache so the next /all reflects it.
    _STATUS_CACHE["payload"] = None
    _STATUS_CACHE["expires_at"] = 0.0
    return {"ok": True, "check_id": check_id, "acked_until": until or None}


# ═══════════════════════════════════════════════════════════════
# Notification bell endpoints (Feb 2026)
#
# UI short-polls /notifications every 10-15s to render the badge.
# All rows live in the health_notifications collection populated by
# services/health_notifier.py.
# ═══════════════════════════════════════════════════════════════

@router.get("/notifications")
async def list_notifications(limit: int = 30):
    """Newest-first list. `unread_count` drives the bell badge."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"available": False, "notifications": [], "unread_count": 0}
    limit = max(1, min(int(limit or 30), 200))
    rows = await db.health_notifications.find({}) \
        .sort("created_at", -1).limit(limit).to_list(None)
    for r in rows:
        r.pop("_id", None)
        # Feb 2026 · Bell-1 · per-row mark-read support.  Give every row
        # a stable client-side identifier: prefer the persisted
        # `notif_id` (written by health_notifier on new rows), fall back
        # to the composite `<check_id>|<created_at>` for legacy rows
        # from before Feb 2026 — the per-row endpoint accepts either.
        if not r.get("notif_id"):
            r["notif_id"] = f"{r.get('check_id')}|{r.get('created_at')}"
    unread = await db.health_notifications.count_documents({"read": False})
    return {
        "available":    True,
        "notifications": rows,
        "unread_count":  unread,
    }


@router.post("/notifications/mark-read")
async def mark_notifications_read():
    """Bell UI 'mark all as read' — clears the badge without deleting
    history."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    r = await db.health_notifications.update_many(
        {"read": False}, {"$set": {"read": True}}
    )
    return {"ok": True, "modified": r.modified_count}


@router.post("/notifications/{notif_id}/mark-read")
async def mark_one_notification_read(notif_id: str):
    """Bell UI per-row 'mark this read' — targets a single notification
    by its stable `notif_id`.  Legacy rows written before Feb 2026 don't
    have `notif_id`; for those we fall back to matching on the composite
    (check_id, created_at) that the client passes as the `notif_id` string
    (format: `<check_id>|<created_at>`).  Never touches history — read=True
    only."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    q: dict
    if "|" in notif_id:
        cid, ts = notif_id.split("|", 1)
        q = {"check_id": cid, "created_at": ts}
    else:
        q = {"notif_id": notif_id}

    r = await db.health_notifications.update_one(q, {"$set": {"read": True}})
    if not getattr(r, "matched_count", 0):
        raise HTTPException(404, "notification not found")
    # Return the fresh unread count so the UI can update its badge
    # from the authoritative server number in the same round-trip
    # (avoids a follow-up GET).
    unread = await db.health_notifications.count_documents({"read": False})
    return {"ok": True, "modified": r.modified_count, "unread_count": unread}


__all__ = ["router"]
