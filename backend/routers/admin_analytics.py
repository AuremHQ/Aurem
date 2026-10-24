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
async def cleanup_e2e_sessions(authorization: Optional[str] = Header(None)):
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
    and organic