"""
routers/onboarding.py — Admin trigger + public click-tracking.

Two endpoints:
  • POST /api/aurem-dev/admin/onboarding/send-connect-nudge
      Admin-only. No per-call cap (user explicitly requested "direct
      send, no cap"). Supports `dry_run=true` to preview the batch
      without sending. Supports `user_ids=[...]` to scope a manual
      send to a subset.
  • GET  /api/aurem-dev/onboarding/click?uid=X&c=connect_repo_nudge
      Public 302 redirector. Logs the click against the most recent
      onboarding_emails row for that user, then bounces them to the
      dashboard with `?action=connect-repo&utm_source=email&utm_campaign=onboarding`
      so the wizard opens automatically.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.onboarding_email import (
    CAMPAIGN, eligible_users, run_nudge_batch,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Onboarding"])


# ── Admin trigger ────────────────────────────────────────────────────
class _NudgeBody(BaseModel):
    dry_run:  bool = False
    stages:   list[str] = ["t24", "t72"]
    user_ids: Optional[list[str]] = None


@router.post("/admin/onboarding/send-connect-nudge")
async def send_connect_nudge(
    body: _NudgeBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger the connect-repo nudge campaign. Admin/founder only.

    `dry_run=true` returns the would-be recipients and a preview of
    the rendered body without calling Resend or writing to Mongo (no
    audit row, no email)."""
    me = await current_dev(authorization)
    if not (me.get("is_admin") or me.get("tier") == "founder"):
        raise HTTPException(403, "Admin access required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    # Validate stage names defensively.
    stages = tuple(s for s in body.stages if s in ("t24", "t72"))
    if not stages:
        raise HTTPException(400, "stages must contain t24 and/or t72")

    if body.dry_run:
        # Preview path — no audit row, no send.
        preview = []
        for stage in stages:
            cohort = await eligible_users(db, stage=stage)
            if body.user_ids:
                keep = set(body.user_ids)
                cohort = [u for u in cohort if u.get("user_id") in keep]
            for u in cohort:
                preview.append({
                    "user_id": u.get("user_id"),
                    "email":   u.get("email"),
                    "name":    u.get("name"),
                    "stage":   stage,
                })
        return {
            "ok":         True,
            "dry_run":    True,
            "stages":     list(stages),
            "recipients": preview,
            "count":      len(preview),
        }

    result = await run_nudge_batch(
        db, stages=stages, dry_run=False,
        user_ids=body.user_ids,
    )
    return result


class _GateStuckNudgeBody(BaseModel):
    dry_run:  bool = False
    user_ids: Optional[list[str]] = None


@router.post("/admin/onboarding/send-gate-stuck-nudge")
async def send_gate_stuck_nudge_admin(
    body: _GateStuckNudgeBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Manual trigger for the fast gate-stuck nudge (services/
    gate_stuck_nudge.py) — same runs automatically on an hourly cron,
    this lets an admin preview the cohort or force a run immediately
    instead of waiting for the next tick. Admin/founder only."""
    me = await current_dev(authorization)
    if not (me.get("is_admin") or me.get("tier") == "founder"):
        raise HTTPException(403, "Admin access required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    from services.gate_stuck_nudge import stuck_gate_users, run_gate_stuck_nudge_batch, send_gate_stuck_nudge

    if body.dry_run:
        cohort = await stuck_gate_users(db)
        if body.user_ids:
            keep = set(body.user_ids)
            cohort = [u for u in cohort if u.get("user_id") in keep]
        recipients = []
        for u in cohort:
            preview = await send_gate_stuck_nudge(u, dry_run=True)
            recipients.append({
                "user_id": u.get("user_id"), "email": u.get("email"),
                "name": u.get("name"), "preview": preview.get("preview"),
            })
        return {"ok": True, "dry_run": True, "recipients": recipients, "count": len(recipients)}

    if body.user_ids:
        cohort = [u for u in await stuck_gate_users(db) if u.get("user_id") in set(body.user_ids)]
        summary = {"ok": True, "dry_run": False, "sent": 0, "skipped": 0, "failed": 0,
                   "recipients": [], "errors": []}
        for u in cohort:
            res = await send_gate_stuck_nudge(u, dry_run=False)
            summary["recipients"].append(res)
            if res.get("skipped"):
                summary["skipped"] += 1
            elif res.get("ok"):
                summary["sent"] += 1
            else:
                summary["failed"] += 1
        return summary

    return await run_gate_stuck_nudge_batch(db, dry_run=False)


# ── Public click tracker ─────────────────────────────────────────────
@router.get("/onboarding/click")
async def onboarding_click(
    uid: str = "",
    c:   str = CAMPAIGN,
    stage: Optional[str] = None,
):
    """Public 302 — logs the click and redirects to the dashboard.

    Always returns a redirect, even when `uid` is missing or the
    audit row can't be located, so a malformed link still drops the
    user on the right page rather than showing an error.

    2026-08-20 — `stage` is optional and used by the newer
    `funnel_stage_nudge` campaign (services/funnel_nudge_cron.py) so
    a click attributes to the exact stage email that was sent (a user
    can receive several different stage nudges over their lifetime,
    unlike the old campaign's single t24/t72 pair). It also decides
    the redirect target: the two GitHub-related stages reopen the
    connect-repo wizard; `stage3_no_chat` users already have a
    project, so they land on the plain dashboard instead.
    """
    action = "connect-repo"
    if stage == "stage3_no_chat":
        action = None

    target_path = "/dashboard?" + urlencode({
        **({"action": action} if action else {}),
        "utm_source":   "email",
        "utm_campaign": c,
    })

    public_base = os.environ.get(
        "PUBLIC_APP_URL", "https://auremcto.com",
    ).rstrip("/")
    target = f"{public_base}{target_path}"

    db = get_db()
    if db is not None and uid:
        try:
            # Latest sent row for this user+campaign(+stage when given).
            # Idempotent: if the user clicks twice we bump
            # `click_count` and update `last_clicked_at`, but
            # `clicked_at` (first-click) sticks.
            now = datetime.now(timezone.utc)
            query: dict = {"user_id": uid, "campaign": c, "sent_ok": True}
            if stage:
                query["stage"] = stage
            doc = await db.onboarding_emails.find_one(
                query, sort=[("sent_at", -1)],
            )
            if doc:
                update: dict = {
                    "$set": {"last_clicked_at": now},
                    "$inc": {"click_count": 1},
                }
                if not doc.get("clicked_at"):
                    update["$set"]["clicked_at"] = now
                await db.onboarding_emails.update_one(
                    {"_id": doc["_id"]}, update,
                )
        except Exception as e:
            logger.warning("onboarding click log failed: %r", e)

    return RedirectResponse(url=target, status_code=302)


# ═════════════════════════════════════════════════════════════════════
# Mandatory Repo-Connect Gate + Post-Connect Engagement Nudge
# (2026-09-09) — founder ticket, see PRD.md for the full spec.
#
# Part 1 state model (deliberately NOT a 3-valued enum in the DB —
# it's a pure function of two independently-true facts, re-checked
# fresh on every chat load):
#   has_project   -> Case A already happened, gate never shows again.
#   gate_skipped  -> Case B happened, gate never shows again.
#   neither       -> Case C (never asked, or abandoned mid-gate) —
#                    gate shows. No extra "abandon" bookkeeping needed:
#                    closing the tab/refreshing just means neither
#                    flag ever got set, so the SAME check re-shows the
#                    gate next time with zero special-case code.
# ═════════════════════════════════════════════════════════════════════

_POST_SKIP_NUDGE_LIFETIME_CAP = 3
_POST_SCAN_FALLBACK_TASK = "Fix the top issue from your scan"


class _GateCompletedBody(BaseModel):
    project_id: Optional[str] = None


class _NudgeShownBody(BaseModel):
    trigger: str  # "post_skip" | "post_scan"
    project_id: Optional[str] = None


@router.get("/onboarding/repo-connect-gate/state")
async def repo_connect_gate_state(authorization: Optional[str] = Header(None)) -> dict:
    """One round-trip for the frontend's gate decision: has this user
    already skipped (Case B) or already connected a repo (Case A)?
    If neither, the gate must show on this chat load."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"skipped": False, "has_project": False, "post_skip_nudge_count": 0}
    user = await db.dev_users.find_one(
        {"user_id": me["user_id"]},
        {"_id": 0, "repo_connect_gate_skipped": 1, "post_skip_nudge_count": 1},
    ) or {}
    has_project = await db.cto_projects.count_documents({"user_id": me["user_id"]}) > 0
    return {
        "skipped": bool(user.get("repo_connect_gate_skipped")),
        "has_project": has_project,
        "post_skip_nudge_count": int(user.get("post_skip_nudge_count") or 0),
    }


@router.post("/onboarding/repo-connect-gate/shown")
async def repo_connect_gate_shown(authorization: Optional[str] = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = get_db()
    if db is not None:
        from services.signup_guards import emit_repo_connect_gate_shown
        await emit_repo_connect_gate_shown(db, user_id=me["user_id"])
    return {"ok": True}


@router.post("/onboarding/repo-connect-gate/skip")
async def repo_connect_gate_skip(authorization: Optional[str] = Header(None)) -> dict:
    """Explicit "Skip for now" click on the mandatory gate (Case B).
    Idempotent: only flips the flag + emits the funnel event the
    FIRST time — the gate never shows again once skipped, so a
    second skip call shouldn't normally happen, but a stray retry
    must not double-log."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True}
    updated = await db.dev_users.find_one_and_update(
        {"user_id": me["user_id"], "repo_connect_gate_skipped": {"$ne": True}},
        {"$set": {"repo_connect_gate_skipped": True}},
        projection={"_id": 0, "user_id": 1},
    )
    if updated:
        from services.signup_guards import emit_repo_connect_gate_skipped
        await emit_repo_connect_gate_skipped(db, user_id=me["user_id"])
    return {"ok": True}


@router.post("/onboarding/repo-connect-gate/completed")
async def repo_connect_gate_completed(
    body: _GateCompletedBody, authorization: Optional[str] = Header(None),
) -> dict:
    """Fired by the gate-mode wizard right after a successful connect
    (Case A). Pure funnel-measurement signal — the gate stops showing
    on its own once `has_project` is true; this event is what tells
    us it was specifically THIS screen that got them there."""
    me = await current_dev(authorization)
    db = get_db()
    if db is not None:
        from services.signup_guards import emit_repo_connect_gate_completed
        await emit_repo_connect_gate_completed(
            db, user_id=me["user_id"], project_id=body.project_id,
        )
    return {"ok": True}


@router.post("/onboarding/nudge/shown")
async def engagement_nudge_shown(
    body: _NudgeShownBody, authorization: Optional[str] = Header(None),
) -> dict:
    """Part 2 — a nudge just rendered in-chat. Records it as the
    'pending' nudge so the next real task message can be attributed
    to it (post_scan_nudge_task_sent). For the post-skip variant,
    also enforces the hard lifetime cap of 3 server-side (frontend
    caps too, but the server is the source of truth)."""
    if body.trigger not in ("post_skip", "post_scan"):
        raise HTTPException(400, "trigger must be post_skip or post_scan")
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "capped": False}
    if body.trigger == "post_skip":
        user = await db.dev_users.find_one(
            {"user_id": me["user_id"]}, {"_id": 0, "post_skip_nudge_count": 1},
        ) or {}
        if int(user.get("post_skip_nudge_count") or 0) >= _POST_SKIP_NUDGE_LIFETIME_CAP:
            return {"ok": True, "capped": True}
        await db.dev_users.update_one(
            {"user_id": me["user_id"]}, {"$inc": {"post_skip_nudge_count": 1}},
        )
    await db.dev_users.update_one(
        {"user_id": me["user_id"]},
        {"$set": {"pending_nudge": {
            "trigger": body.trigger, "shown_at": time.time(),
            "project_id": body.project_id, "fired": False,
        }}},
    )
    from services.signup_guards import emit_engagement_nudge_shown
    await emit_engagement_nudge_shown(
        db, user_id=me["user_id"], trigger=body.trigger, project_id=body.project_id,
    )
    return {"ok": True, "capped": False}


@router.get("/onboarding/nudge/post-scan-state")
async def post_scan_nudge_state(
    project_id: Optional[str] = None, authorization: Optional[str] = Header(None),
) -> dict:
    """Part 2 trigger condition 2: connected + ran a scan or opened
    the graph, but zero real task messages sent since. `prefill_task`
    pulls the real scan's `top_category`; if that's missing for any
    reason, ships the exact named fallback task — never a blank or
    generic placeholder. Frontend wraps it as `Try: '{prefill_task}'`
    for display and uses it verbatim to prefill the chat composer."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"show": False, "prefill_task": None}

    latest = await db.funnel_events.find(
        {"user_id": me["user_id"], "event_type": {"$in": ["first_scan_completed", "graph_built"]}},
        {"_id": 0, "ts_epoch": 1},
    ).sort("ts_epoch", -1).limit(1).to_list(1)
    if not latest:
        return {"show": False, "prefill_task": None}
    scan_ts = latest[0].get("ts_epoch") or 0

    user = await db.dev_users.find_one(
        {"user_id": me["user_id"]}, {"_id": 0, "last_real_task_message_at": 1},
    ) or {}
    last_task_at = user.get("last_real_task_message_at") or 0
    if last_task_at >= scan_ts:
        return {"show": False, "prefill_task": None}

    top_scan = await db.funnel_events.find_one(
        {"user_id": me["user_id"], "event_type": "first_scan_completed"},
        {"_id": 0, "metadata": 1}, sort=[("ts_epoch", -1)],
    )
    top_category = ((top_scan or {}).get("metadata") or {}).get("top_category")
    prefill_task = (
        f"Fix the top issue you found — {top_category}"
        if top_category else _POST_SCAN_FALLBACK_TASK
    )
    return {"show": True, "prefill_task": prefill_task}
