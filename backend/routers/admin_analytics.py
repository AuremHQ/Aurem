"""admin_analytics.py — Analytics, telemetry, observability, dashboard, council, loop metrics.

Extracted from routers/admin.py during Phase 2 architecture split (2026-02-11).
Contains 42 handler(s)/helper(s):

  GET  /admin/dashboard    /admin/audit    /admin/pulse    /admin/system-stats
  GET  /admin/token-pnl   /admin/agent-tokens   /admin/overview-metrics
  GET  /admin/mcp-usage   /admin/graph-status   /admin/agent-performance
  GET  /admin/loop-metrics /admin/loop-token-metrics /admin/loop-inspect/{loop_id}
  GET  /admin/speed-diagnostic /admin/scope-drift-audit
  GET  /admin/eval-quality /admin/mode-telemetry /admin/product-analytics
  GET  /admin/vanguard/stats /admin/vanguard/recent
  GET  /admin/warm-start-stats /admin/skills-usage /admin/skills/status
  GET  /admin/failure-patterns
  POST /admin/skills/* (web-search, fetch-url, firecrawl-*, search-and-summarize)
  GET  /admin/council/* /admin/ora/*  POST /admin/qa/cleanup-e2e-sessions
  POST /admin/seo/run
  GET  /admin/digest /admin/learning-health

Every handler + helper is COPIED VERBATIM from the pre-split admin.py.
"""
from __future__ import annotations

import logging
import os
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from pydantic import BaseModel

from cto_services.auth import require_admin_dep
from cto_services.db import get_db, require_db
# Iter 212m-71 — 60 s TTL cache for the heavy admin aggregations
# (activation funnel, dev_users buckets, etc.). Founders click around
# the admin panel rapidly; without this every click fires 5+ heavy
# aggregations against Mongo.

logger = logging.getLogger(__name__)
# Iter 358 — router-level admin gate (defense-in-depth). EVERY route on
# this router is denied to non-founders at the router boundary, so a new
# endpoint added later is protected by default. Individual handlers keep
# their inline `await _require_admin(...)` too (harmless redundancy).
# The one intentionally-public sink (/admin/errors/report) lives on the
# separate, un-gated routers/admin_public.py at the same URL.

router = APIRouter(
    prefix="/admin", tags=["Admin-analytics"],
    dependencies=[Depends(require_admin_dep)],
)

from routers._admin_common import _require_admin  # noqa: E402
# 2026-02-11 · Phase 2 split fix — helpers still live in the pre-split
# admin.py stub. Re-import here so handler bodies still resolve them.
# (Testing agent iter364 caught these as latent NameError bugs.)
from routers.admin import _bucket_label, _run_skill, _email_hint  # noqa: E402


@router.post("/qa/cleanup-e2e-sessions")
async def cleanup_e2e_sessions(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    # Reject oversized request bodies to prevent DoS — this endpoint
    # needs no body at all, so cap at 1 KB.
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 1024:
        raise HTTPException(413, "Request body too large")
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    from services.test_accounts import E2E_SESSION_PREFIX_RE
    res = await db.chat_sessions.delete_many(
        {"session_id": E2E_SESSION_PREFIX_RE})
    return {"ok": True, "deleted": res.deleted_count}


@router.get("/audit")
async def audit_feed(
    limit:        int = 100,
    user_id:      Optional[str] = None,
    project_id:   Optional[str] = None,
    signals_only: bool = False,
    authorization: Optional[str] = Header(None),
):
    """Read the `ora_audit` collection. Backed by `audit_log.list_turns`.
    Used by the admin panel's Audit tab to surface every ORA turn,
    citation-guard triggers, and tool-error signals.
    """
    await _require_admin(authorization)
    from services.audit_log import list_turns
    rows = await list_turns(
        user_id=user_id,
        project_id=project_id,
        limit=max(1, min(int(limit or 100), 500)),
        signals_only=signals_only,
    )
    return {"ok": True, "rows": rows, "count": len(rows)}


@router.get("/dashboard")
async def dashboard(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    day_ago = now - 86400

    total_users = await db.dev_users.count_documents({})
    total_tasks = await db.cto_tasks.count_documents({})
    tasks_today = await db.cto_tasks.count_documents({"created_at": {"$gte": day_ago}})
    failed_tasks = await db.cto_tasks.count_documents({"status": "failed"})
    done_tasks = await db.cto_tasks.count_documents({"status": "done"})
    total_projects = await db.cto_projects.count_documents({})
    total_sessions = await db.chat_sessions.count_documents({})

    recent_tasks = await db.cto_tasks.find(
        {}, {"_id": 0, "steps": 0, "rollback_steps": 0}
    ).sort("created_at", -1).limit(5).to_list(5)

    recent_users = await db.dev_users.find(
        {}, {"_id": 0, "password": 0, "password_hash": 0, "github.access_token": 0}
    ).sort("created_at", -1).limit(5).to_list(5)

    return {
        "total_users": total_users,
        "total_tasks": total_tasks,
        "tasks_today": tasks_today,
        "failed_tasks": failed_tasks,
        "done_tasks": done_tasks,
        "success_rate": round((done_tasks / max(total_tasks, 1)) * 100, 1),
        "total_projects": total_projects,
        "total_sessions": total_sessions,
        "recent_tasks": recent_tasks,
        "recent_users": recent_users,
    }


@router.get("/pulse")
async def business_pulse(authorization: Optional[str] = Header(None)):
    """Feb 2026 — cockpit Business Pulse endpoint. Returns BOTH raw
    and organic-only counts, plus explicit env tag so a preview
    reading is never mistaken for a production reading again.

    Organic filter uses services.synthetic_filter (single source of
    truth also consumed by G2 marketing-truth guard). Env tag comes
    from services.env_context — same helper backs the cockpit
    'PREVIEW DATA' badge.

    Feb 2026 · prod-hang fix — original implementation ran 8 sequential
    `count_documents(...)` calls; if any one query stalled (missing
    index on a large collection) the whole endpoint hung forever.
    Now runs them concurrently via asyncio.gather with a hard 10s
    outer timeout so a slow query surfaces as a fast error, not a
    permanently pending request."""
    await _require_admin(authorization)
    db = require_db()
    from services.synthetic_filter import synthetic_mongo_filter
    from services.env_context import env_stamp

    org_filter = synthetic_mongo_filter()
    gh_filter  = {"github.access_token": {"$exists": True, "$nin": [None, ""]}}
    # Closing-the-bypass-doors audit (Step 5a) — this used to read
    # ["basic", "pro", "maxx", "founder"], which confuses the chat-MODE
    # vocabulary ("swift"/"pro"/"maxx" — a UI pill, see
    # services/subscription_tiers.py's `modes` field) with the actual
    # dev_users.tier vocabulary (Tier enum: free/starter/pro/team/
    # founder). "basic" and "maxx" never appear as a real stored tier
    # value anywhere (confirmed by grep) — the old filter silently
    # missed every "starter" and "team" user in this paid-count metric.
    paid_tiers = ["starter", "pro", "team", "founder"]
    paid_raw_filter = {"tier": {"$in": paid_tiers}}

    from datetime import datetime, timezone, timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    paid_new_raw_filter = {**paid_raw_filter, "created_at": {"$gte": since}}

    try:
        (
            total_users_raw, total_users_organic,
            gh_raw, gh_organic,
            paid_users_raw, paid_users_organic,
            paid_new_30d_raw, paid_new_30d_organic,
        ) = await asyncio.wait_for(
            asyncio.gather(
                db.dev_users.count_documents({}),
                db.dev_users.count_documents(org_filter),
                db.dev_users.count_documents(gh_filter),
                db.dev_users.count_documents({**org_filter, **gh_filter}),
                db.dev_users.count_documents(paid_raw_filter),
                db.dev_users.count_documents({**org_filter, **paid_raw_filter}),
                db.dev_users.count_documents(paid_new_raw_filter),
                db.dev_users.count_documents({**org_filter, **paid_new_raw_filter}),
            ),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            504, "business pulse timed out after 10s — check Mongo indexes on dev_users"
        )

    def _pct(num, denom):
        return round(100.0 * num / denom, 1) if denom else 0.0

    return {
        # Environment identification — added Feb 2026 after
        # preview/prod denominator confusion audit.
        **env_stamp(),
        # Raw (unfiltered) — same as before.
        "raw": {
            "total_users":         total_users_raw,
            "github_connected":    gh_raw,
            "github_connect_pct":  _pct(gh_raw, total_users_raw),
            "paid_users":          paid_users_raw,
            "paid_new_30d":        paid_new_30d_raw,
        },
        # Organic — synthetic test/bot rows excluded. This is the
        # denominator the cockpit renders.
        "organic": {
            "total_users":         total_users_organic,
            "github_connected":    gh_organic,
            "github_connect_pct":  _pct(gh_organic, total_users_organic),
            "paid_users":          paid_users_organic,
            "paid_new_30d":        paid_new_30d_organic,
        },
        # Convenience mirror for the current cockpit code that reads
        # top-level fields; will migrate to explicit organic.* soon.
        "total_users":         total_users_organic,
        "github_connected":    gh_organic,
        "github_connect_pct":  _pct(gh_organic, total_users_organic),
        "paid_users":          paid_users_organic,
        "paid_new_30d":        paid_new_30d_organic,
    }


@router.get("/system-stats")
async def admin_system_stats(
    window_hours: int = 24,
    authorization: Optional[str] = Header(None),
):
    """Aggregates the 4 observability collections written by recent
    iters (parliament_log, intent_classifications, quality_scores,
    plus syntax_gate metrics derived from chat.py logs)."""
    await _require_admin(authorization)
    db = require_db()
    now_ts  = time.time()
    cutoff  = now_ts - (max(1, min(window_hours, 24 * 30)) * 3600)
    cutoff_24h = now_ts - 86400

    # ── Parliament ────────────────────────────────────────────────
    parl_total = await db.parliament_log.count_documents(
        {"event": "aggregate", "ts": {"$gte": cutoff}}
    )
    parl_success = await db.parliament_log.count_documents(
        {"event": "aggregate", "status": "success",
         "ts": {"$gte": cutoff}}
    )
    parl_review = await db.parliament_log.count_documents(
        {"event": "aggregate", "status": "manual_review",
         "ts": {"$gte": cutoff}}
    )
    parl_cb_opens_24h = await db.parliament_log.count_documents(
        {"event": "circuit_open_fallback", "ts": {"$gte": cutoff_24h}}
    )
    # Council A member-win distribution.
    council_win_pipeline = [
        {"$match": {"event": "aggregate", "status": "success",
                    "council": "A", "ts": {"$gte": cutoff}}},
        {"$group": {"_id": "$winner", "n": {"$sum": 1}}},
    ]
    win_rows = await db.parliament_log.aggregate(council_win_pipeline).to_list(20)
    council_A_wins: dict[str, int] = {
        "A1-conservative": 0, "A2-balanced": 0, "A3-creative": 0,
    }
    for row in win_rows:
        key = row.get("_id") or "unknown"
        if key in council