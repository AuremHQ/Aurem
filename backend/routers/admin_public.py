"""
routers/admin_public.py — Iter 358.

The ONE intentionally-public endpoint that historically lived under the
/admin prefix: the frontend console-error sink. It must stay reachable
without auth (logged-out users hit console errors too), so it cannot
live on the now router-gated admin router.

Kept at the SAME URL (/admin/errors/report) so no frontend change is
needed — this router mounts with prefix "/admin" and NO admin
dependency, and only exposes this single write-only endpoint that
returns no data.

2026-09-29 · security hardening (item 1):
  Being unauthenticated by design, this sink is also globally exempt
  from the shared request-rate middleware (main.py,
  _GLOBAL_RL_SKIP_PREFIXES) — the frontend ErrorBoundary/console hook
  retries on failure, and a 429 there would just storm the endpoint
  harder. That exemption made three things attacker-reachable with NO
  guard at all:
    1. Unbounded distinct (message, url) upserts — every unique pair
       is a permanent-for-90-days new document, so a script POSTing
       random urls could grow the collection without limit.
    2. No per-IP throttle at all (client-side dedupe in
       errorReporter.js is trivially bypassed by anyone calling the
       endpoint directly).
    3. `type` was a free-form string with no allowlist.
  Fixes, all enforced HERE (still no auth, still always HTTP 200 — an
  errored-out sink must never itself become a source of retry storms):
    - Per-IP cap via services.rate_limiter (30/min). Over the cap:
      `{"ok": false, "error": "rate_limited"}`, HTTP 200.
    - Global cap on NEW distinct rows created in the trailing hour
      (updates to an already-existing (message, url) row are free —
      only fresh inserts count against the budget). Counted via the
      Mongo `_id` field itself (ObjectId encodes its creation second),
      so no new index or field is needed. Over the cap: same
      `{"ok": false, "error": "rate_limited"}` shape.
    - `type` is now allowlisted to the 3 values errorReporter.js
      actually sends; anything else is coerced to "console_error"
      rather than rejected (this is a diagnostics sink, not a place to
      start bouncing genuine reports over a typo).
    - Field caps lowered (message 4000→1000, stack 16000→4000, url
      1000→400).
    - Every row is stamped `trusted: false` — this sink has no auth,
      so nothing it writes can ever be treated as admin-originated
      data. The admin Errors UI uses this to render an "untrusted"
      badge and to never treat message/stack as anything but plain
      text (see routers/admin_support.py list_errors).
  The existing 90-day TTL index on `last_seen_at`
  (scripts/init_prod_collections.py) is untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from bson import ObjectId
from fastapi import APIRouter, Request
from pydantic import BaseModel

from cto_services.db import get_db
from services.rate_limiter import check_rate_limit_async, client_ip_from_request

router = APIRouter(prefix="/admin", tags=["Admin-public"])

# The only 3 values errorReporter.js ever sends (console.error hook,
# unhandledrejection, window.onerror) — see frontend/src/utils/errorReporter.js.
_ALLOWED_TYPES = frozenset({"console_error", "unhandled_rejection", "window_error"})

_PER_IP_LIMIT_PER_MIN = 30
_MAX_NEW_ROWS_PER_HOUR = 200


class ErrorReport(BaseModel):
    message:   str
    stack:     str = ""
    url:       str = ""
    timestamp: str = ""
    type:      str = "console_error"


@router.post("/errors/report")
async def report_error(body: ErrorReport, request: Request) -> dict:
    """Public — no auth. Frontend posts every console.error here.
    Write-only: dedupes by (message, url), returns only {ok}. No admin
    data is ever read or returned, so keeping it unauthenticated is
    safe (the actual abuse surface — unbounded row growth and request
    volume — is throttled below, not by removing the endpoint)."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "db_unavailable"}

    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"errreport:{ip}", _PER_IP_LIMIT_PER_MIN):
        # Deliberately 200, not 429 — the ErrorBoundary/console hook
        # retries on failure, and a non-2xx here would just make the
        # storm worse instead of backing off.
        return {"ok": False, "error": "rate_limited"}

    now = datetime.now(timezone.utc)
    msg   = (body.message or "")[:1_000]
    stack = (body.stack   or "")[:4_000]
    url   = (body.url     or "")[:400]
    err_type = body.type if body.type in _ALLOWED_TYPES else "console_error"
    if not msg.strip():
        return {"ok": False, "error": "empty_message"}

    existing = await db.frontend_errors.find_one(
        {"message": msg, "url": url}, {"_id": 1})
    if existing is None:
        hour_ago = ObjectId.from_datetime(now - timedelta(hours=1))
        new_rows_last_hour = await db.frontend_errors.count_documents(
            {"_id": {"$gte": hour_ago}})
        if new_rows_last_hour >= _MAX_NEW_ROWS_PER_HOUR:
            return {"ok": False, "error": "rate_limited"}

    update = {
        "$inc": {"count": 1},
        "$set": {
            "last_seen":  now.isoformat(),
            "last_seen_at": now,
            "stack":      stack,
            "type":       err_type,
            "user_agent": (request.headers.get("user-agent") or "")[:500],
        },
        "$setOnInsert": {
            "message":    msg,
            "url":        url,
            "first_seen": now.isoformat(),
            "resolved":   False,
            "autofix_status": "idle",
            "trusted":    False,
        },
    }
    await db.frontend_errors.update_one(
        {"message": msg, "url": url}, update, upsert=True)
    return {"ok": True}
