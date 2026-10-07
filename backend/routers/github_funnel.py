"""
routers/github_funnel.py — GitHub Connect funnel telemetry (2026-08-01)

Tracks 5 real-user stages of the GitHub-connect flow so we can pinpoint
where the drop-off happens BEFORE guessing which specific UX bug to
fix. Data is collected from real new signups going forward; the
existing 41-user cohort is not backfilled (only forward events).

Stages (in order):
  1. cta_click        — user clicks "Connect GitHub" (frontend)
  2. oauth_redirect   — backend /connect redirects to github.com
  3. callback_received — backend /callback hit (success OR error)
  4. linked           — github.access_token stored in dev_users
  5. repo_selected    — user picks a repo (post-link action)

Anonymous ingestion (no auth required for /event) so pre-signup CTA
clicks are captured. Admin-only /stats aggregates counts per stage.

Mounted at /api/aurem-dev/funnel/github/*
"""
from __future__ import annotations
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from cto_services.auth import JWT_ALGORITHM, JWT_SECRET, current_dev
from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/funnel/github", tags=["GitHub Funnel"])

# ── Canonical stage names ─────────────────────────────────────────────
STAGES = (
    "cta_click",
    "oauth_redirect",
    "callback_received",
    "linked",
    "repo_selected",
    # 2026-02-10 · Phase 2 · GitHub App install flow stages.
    "app_install_redirect",  # server: /github/app/install → GitHub
    "app_installed",         # server: /github/app/callback success
    "app_repo_selected",     # client: user picks a repo from installation
    # 2026-08-27 · Journey Watch Phase 0 — closes the F1 dark-click gap.
    # `connect_repo_click` fires from BOTH untracked CTAs identified in
    # the signup drop-off investigation: ConnectRepoBanner's "Connect
    # repo →" and NewUserWizard's "Continue with GitHub App". Fired
    # BEFORE the popup-open attempt, so — unlike app_install_redirect —
    # it lands even when the popup is blocked.
    "connect_repo_click",
    # Client-inferred: the install popup closed WITHOUT the connect
    # status ever reaching "connected" (GitHub Apps don't send a real
    # deny callback the way OAuth Apps do — closing the tab is the
    # only observable signal). meta.reason is "popup_closed" or
    # "client_timeout". Journey Watch treats this as a hard-break.
    "app_install_denied",
    # 2026-08-27 · Journey Watch Phase 0 — canonical funnel schema.
    # `github_auth_started` fires the instant the install/OAuth popup
    # actually opens (as opposed to `connect_repo_click`, which fires
    # even on `popup_blocked`) — lets Journey Watch tell "clicked" apart
    # from "actually reached GitHub". `app_install_granted` fires when
    # the App install is confirmed (client poll AND server callback
    # both emit it, so a closed tab doesn't hide a real grant).
    # `project_connected` fires server-side the moment a project row is
    # actually created — the funnel's ground truth for "repo connected".
    "github_auth_started",
    "app_install_granted",
    "project_connected",
)

# Sources where the CTA lives — used to segment drop-off by entry point.
SOURCES = (
    "login",           # /login page GitHub button
    "signup",          # /signup page GitHub button
    "settings_card",   # GitHubCard on /settings
    "wizard",          # NewUserWizard step
    "projects",        # Projects page inline connect
    "banner",          # 2026-08-27 · ConnectRepoBanner "Connect repo →"
    "unknown",         # fallback
)


class FunnelEvent(BaseModel):
    stage: str
    source: str = "unknown"
    session_id: str = Field(..., min_length=8, max_length=64)
    user_id: Optional[str] = None
    meta: Optional[dict] = None


def _check_meta_size(meta: Optional[dict]) -> None:
    """2026-09-30 — SECURITY FIX (Medium, code-read audit): this
    endpoint is intentionally anonymous (pre-signup CTA clicks have no
    session yet), which meant `meta` was an arbitrary, unauthenticated
    client-controlled dict with no size limit at all — a caller could
    bloat github_funnel_events with huge documents (DB/storage cost)
    or poison the /stats aggregation (meta.reason/meta.outcome feed
    directly into the admin funnel report). Capped to a size generous
    enough for real telemetry (short reason/outcome strings) but far
    too small to be useful for abuse.

    Deliberately a plain function raising HTTPException from inside
    the endpoint, NOT a pydantic @field_validator: this app's global
    `_validation_exc_handler` (main.py) calls `exc.errors()` and JSON-
    encodes it directly — pydantic puts the raw exception object in
    `ctx.error` for a validator-raised ValueError, which is not JSON
    serializable and crashes that handler into an unrelated 499. Not
    fixing that shared, pre-existing handler here to keep this PR
    scoped to the actual finding; this sidesteps it entirely.
    """
    if meta is None:
        return
    if len(meta) > 20:
        raise HTTPException(422, "meta: too many keys (max 20)")
    try:
        encoded = json.dumps(meta)
    except (TypeError, ValueError):
        raise HTTPException(422, "meta: must be JSON-serializable")
    if len(encoded) > 2048:
        raise HTTPException(422, "meta: too large (max 2048 bytes serialized)")


def _best_effort_authenticated_user_id(authorization: Optional[str]) -> Optional[str]:
    """2026-09-30 — SECURITY FIX (Medium): `user_id` on this anonymous
    endpoint used to be taken verbatim from the client body — any
    unauthenticated caller could claim to BE any real user_id, poisoning
    that user's funnel/conversion data. This endpoint must stay open to
    logged-out callers (that's the whole point — pre-signup CTA clicks),
    so we can't require auth here. Instead: if a valid session token is
    present, the SERVER derives user_id from it (client's own body value
    is ignored either way); if not, user_id is always None. Deliberately
    NOT `current_dev()` — that raises 401, which would break the
    anonymous case this endpoint exists for. Never raises; any decode
    failure just means "anonymous", the same as no header at all.
    """
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    try:
        payload = jwt.decode(parts[1], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    uid = payload.get("user_id")
    return uid if isinstance(uid, str) else None


async def track_server_side(
    stage: str,
    *,
    source: str = "unknown",
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    meta: Optional[dict] = None,
) -> None:
    """Fire-and-forget helper used by github_oauth.py at each
    backend milestone (oauth_redirect, callback_received, linked).

    Silent-fail: telemetry MUST never break the OAuth flow.
    """
    if stage not in STAGES:
        return
    db = get_db()
    if db is None:
        return
    try:
        doc = {
            "event_id":   uuid.uuid4().hex,
            "stage":      stage,
            "source":     source if source in SOURCES else "unknown",
            "session_id": session_id or f"srv:{uuid.uuid4().hex[:16]}",
            "user_id":    user_id,
            "meta":       meta or {},
            "ts":         time.time(),
            "created_at": datetime.now(timezone.utc),
            "origin":     "server",
        }
        await db.github_funnel_events.insert_one(doc)
    except Exception as e:
        # Never propagate — telemetry is best-effort.
        logger.warning(f"[funnel] server-side track failed: {e!r}")


@router.post("/event")
async def ingest_event(
    evt: FunnelEvent,
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Anonymous ingestion endpoint. Frontend fires this at each
    client-side stage (cta_click, repo_selected). No auth required so
    logged-out users' CTA clicks on /login and /signup are captured.
    """
    if evt.stage not in STAGES:
        raise HTTPException(400, f"unknown stage: {evt.stage}")
    _check_meta_size(evt.meta)
    src = evt.source if evt.source in SOURCES else "unknown"
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    # 2026-09-30 — SECURITY FIX: user_id is now always server-derived
    # (see _best_effort_authenticated_user_id docstring), never taken
    # from evt.user_id — an anonymous caller can no longer attribute
    # events to an arbitrary victim's user_id.
    doc = {
        "event_id":   uuid.uuid4().hex,
        "stage":      evt.stage,
        "source":     src,
        "session_id": evt.session_id,
        "user_id":    _best_effort_authenticated_user_id(authorization),
        "meta":       evt.meta or {},
        "ts":         time.time(),
        "created_at": datetime.now(timezone.utc),
        "origin":     "client",
        # UA/host for later debugging — not PII-sensitive.
        "ua":         (request.headers.get("user-agent") or "")[:200],
    }
    await db.github_funnel_events.insert_one(doc)
    return {"ok": True, "event_id": doc["event_id"]}


@router.get("/stats")
async def funnel_stats(
    authorization: Optional[str] = Header(None),
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """Admin-only aggregate: counts per stage + per source in the
    last `days` window. Also computes conversion% between adjacent
    stages so the drop-off point is one glance away.
    """
    user = await current_dev(authorization)
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin only")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    since = datetime.now(timezone.utc) - timedelta(days=days)
    match = {"created_at": {"$gte": since}}

    # 2026-09-22 · Revenue Recovery audit — events tied to a real
    # user_id can be checked against the synthetic-account filter
    # (services/synthetic_filter.py) so QA/e2e traffic doesn't inflate
    # "real" conversion numbers. Anonymous pre-signup events (cta_click,
    # oauth_redirect before signup) carry no user_id and can't be
    # classified this way — left in, matching production reality where
    # there is no synthetic anonymous traffic.
    from services.synthetic_filter import is_synthetic
    user_ids = await db.github_funnel_events.distinct(
        "user_id", {"created_at": {"$gte": since}, "user_id": {"$ne": None}},
    )
    synthetic_ids: set[str] = set()
    if user_ids:
        cur = db.dev_users.find(
            {"user_id": {"$in": user_ids}}, {"_id": 0, "user_id": 1, "email": 1},
        )
        async for u in cur:
            if is_synthetic(u.get("email")):
                synthetic_ids.add(u["user_id"])

    # Unique sessions that reached each stage — the funnel measure.
    per_stage: dict[str, int] = {s: 0 for s in STAGES}
    per_source_stage: dict[str, dict[str, int]] = {
        src: {s: 0 for s in STAGES} for src in SOURCES
    }
    # Failure-rate-by-stage — the actual "is OAuth the blocker?" signal.
    # `callback_received` is the one stage with multiple outcomes; every
    # other stage is binary (reached it or not), so only that stage needs
    # a reason breakdown.
    callback_outcomes: dict[str, int] = {}
    excluded_synthetic_events = 0

    pipeline = [
        {"$match": match},
        # Dedupe by (session_id, stage) so a single user firing the
        # same stage twice (e.g. rapid double-click) doesn't inflate.
        {"$group": {
            "_id":    {"session_id": "$session_id", "stage": "$stage",
                       "source": "$source", "user_id": "$user_id"},
            "count":  {"$sum": 1},
            "reasons": {"$push": "$meta.reason"},
            "outcomes": {"$push": "$meta.outcome"},
        }},
    ]
    async for row in db.github_funnel_events.aggregate(pipeline):
        stage   = row["_id"]["stage"]
        source  = row["_id"]["source"]
        uid     = row["_id"].get("user_id")
        if uid and uid in synthetic_ids:
            excluded_synthetic_events += 1
            continue
        if stage in per_stage:
            per_stage[stage] += 1
        if source in per_source_stage and stage in per_source_stage[source]:
            per_source_stage[source][stage] += 1
        if stage == "callback_received":
            # 2026-09-30 — SECURITY/DEPLOY FIX: `.get("reasons", [None])[0]`
            # only falls back to `[None]` when the key is entirely
            # MISSING. If `$push` produced an empty list `[]` for this
            # group (real production/Atlas data shape that didn't
            # reproduce with this sandbox's local Mongo — e.g. a
            # `$group` re-run against a stage that predates the `meta`
            # field existing at all), `.get(...)` returns that empty
            # list as-is (the default only applies to a missing key,
            # not a falsy value), and `[][0]` raises IndexError,
            # crashing every subsequent call to this endpoint. `or`
            # falls back to `[None]` for BOTH "missing" and "empty".
            outcome = (row.get("outcomes") or [None])[0]
            reason  = (row.get("reasons") or [None])[0]
            key = reason if (outcome and outcome != "success" and reason) else (outcome or "success")
            callback_outcomes[key] = callback_outcomes.get(key, 0) + 1

    # Compute stage-to-stage conversion %.
    conversions: list[dict] = []
    for i in range(len(STAGES) - 1):
        prev_s, next_s = STAGES[i], STAGES[i + 1]
        prev_n, next_n = per_stage[prev_s], per_stage[next_s]
        pct = (next_n / prev_n * 100.0) if prev_n > 0 else 0.0
        conversions.append({
            "from":     prev_s,
            "to":       next_s,
            "from_n":   prev_n,
            "to_n":     next_n,
            "conv_pct": round(pct, 1),
        })

    callback_total = sum(callback_outcomes.values())
    callback_failure_rate = (
        round((callback_total - callback_outcomes.get("success", 0)) / callback_total * 100.0, 1)
        if callback_total else 0.0
    )

    return {
        "ok":              True,
        "window_days":     days,
        "since":           since.isoformat(),
        "stages":          per_stage,
        "conversions":     conversions,
        "by_source":       per_source_stage,
        "total_events":    sum(per_stage.values()),
        # Granular OAuth failure visibility — answers "is OAuth actually
        # the activation blocker?" by reason, not just success/fail.
        "callback_outcomes":      callback_outcomes,
        "callback_failure_rate_pct": callback_failure_rate,
        "excluded_synthetic_sessions": excluded_synthetic_events,
    }
