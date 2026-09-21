"""
routers/admin_onboarding_funnel.py — Onboarding Funnel dashboard (2026-09-08)

Founder's ask: "onboarding funnel ko accurate karo aur trackable banao
jisse hum pta chal sake kyun user stuck ho gaya hai" — make it possible
to SEE, on demand, exactly where every non-activated user is stuck and
why, without a production DB curl.

Zero new tracking pipeline — this is a thin read-only admin surface on
top of the ALREADY-CORRECT classification logic in
`services/funnel_nudge_cron.classify_users()` (the same function that
decides who gets a stage nudge email), plus the existing
`onboarding_emails` audit collection for nudge sent/click status. No
new collection, no new event, no new cron.

Mounted at /api/aurem-dev/admin/onboarding-funnel/*
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from cto_services.auth import require_admin_dep
from cto_services.db import require_db
from services.casl_consent import casl_screen
from services.funnel_nudge_cron import STAGES, SUBJECTS, classify_users, render_html, render_text

SNAPSHOT_COLLECTION = "onboarding_funnel_snapshots"

router = APIRouter(
    prefix="/admin/onboarding-funnel", tags=["Admin — Onboarding Funnel"],
    dependencies=[Depends(require_admin_dep)],
)

# Natural funnel progression order (earliest → most advanced) — the
# reverse of STAGES, which is ordered by classification-waterfall
# priority (most-advanced-wins). Display order should read like a
# funnel, not like the internal if/elif chain.
STAGE_ORDER = (
    "stage4_fully_inactive",
    "stage1_github_started",
    "stage2_project_pending",
    "stage3_no_chat",
)

STAGE_META = {
    "stage4_fully_inactive": {
        "label":       "Signed up, never started",
        "why":         "Created an account but hasn't clicked Connect GitHub yet.",
    },
    "stage1_github_started": {
        "label":       "Started GitHub connect, didn't finish",
        "why":         "Clicked Connect GitHub but the install/OAuth flow never completed — no repo linked.",
    },
    "stage2_project_pending": {
        "label":       "GitHub connected, no project yet",
        "why":         "Granted repo access but never picked a repo and clicked Continue.",
    },
    "stage3_no_chat": {
        "label":       "Project connected, never sent a task",
        "why":         "Repo is connected and indexed but they've never completed a real task (may have chatted without shipping).",
    },
}


def _iso(epoch: Optional[float]) -> Optional[str]:
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (ValueError, OSError):
        return None


def _stuck_days(epoch: Optional[float], now_epoch: float) -> Optional[int]:
    if not epoch:
        return None
    return int((now_epoch - epoch) // 86400)


@router.get("/summary")
async def onboarding_funnel_summary():
    """Aggregate count per stage, in natural funnel order, so the
    founder sees at a glance where the biggest pile-up is."""
    db = require_db()
    buckets = await classify_users(db)
    now_epoch = datetime.now(timezone.utc).timestamp()

    stages = []
    total_stuck = 0
    for key in STAGE_ORDER:
        users = buckets.get(key, [])
        stuck_days = [
            _stuck_days(u.get("_stuck_since"), now_epoch) for u in users
        ]
        stuck_days = [d for d in stuck_days if d is not None]
        meta = STAGE_META[key]
        total_stuck += len(users)
        stages.append({
            "key":              key,
            "label":            meta["label"],
            "why":              meta["why"],
            "count":            len(users),
            "oldest_stuck_days": max(stuck_days) if stuck_days else None,
            "median_stuck_days": (
                sorted(stuck_days)[len(stuck_days) // 2] if stuck_days else None
            ),
        })

    return {
        "generated_at":          datetime.now(timezone.utc).isoformat(),
        "stuck_threshold_hours": 24,
        "total_stuck":           total_stuck,
        "stages":                stages,
    }


@router.get("/preview-email")
async def onboarding_funnel_preview_email(
    stage: str = Query(..., description="One of: " + ", ".join(STAGES)),
):
    """Render the exact nudge email a stuck user in this stage would
    receive — READ-ONLY, mints no real magic-login token, sends
    nothing. Uses the most-stuck real user in the stage as the
    template (falls back to a placeholder if the stage is empty)."""
    if stage not in STAGES:
        raise HTTPException(400, f"unknown stage: {stage}")
    db = require_db()
    buckets = await classify_users(db)
    users = buckets.get(stage, [])
    if users:
        sample = min(users, key=lambda u: u.get("_stuck_since") or float("inf"))
    else:
        sample = {"user_id": "preview", "email": "preview@example.com", "name": "Preview User"}

    text = await render_text(db, sample, stage, preview=True)
    html = await render_html(db, sample, stage, preview=True)
    return {
        "stage":         stage,
        "label":         STAGE_META[stage]["label"],
        "subject":       SUBJECTS[stage],
        "text":          text,
        "html":          html,
        "sample_email":  sample.get("email"),
        "is_placeholder": not bool(users),
    }


async def capture_funnel_snapshot(db) -> dict:
    """Compute today's stage counts and upsert one snapshot doc for
    today's UTC date — idempotent, safe to call more than once a day
    (the trend line just gets the latest value for that date)."""
    buckets = await classify_users(db)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stage_counts = {key: len(buckets.get(key, [])) for key in STAGE_ORDER}
    doc = {
        "date":         today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stages":       stage_counts,
        "total_stuck":  sum(stage_counts.values()),
    }
    await db[SNAPSHOT_COLLECTION].update_one(
        {"date": today}, {"$set": doc}, upsert=True,
    )
    return doc


async def funnel_snapshot_cron(interval_seconds: int = 86400, *, startup_delay_seconds: int = 60) -> None:
    """Daily loop — one snapshot/day so the Onboarding Funnel trend
    line has history. Mirrors funnel_nudge_cron.nudge_cron's
    startup-delay pattern (avoids double-writing during a rolling
    deploy cutover; upsert-by-date makes a duplicate tick harmless
    either way)."""
    import asyncio

    from cto_services.db import get_db
    await asyncio.sleep(startup_delay_seconds)
    while True:
        try:
            db = get_db()
            if db is not None:
                await capture_funnel_snapshot(db)
        except Exception:
            pass
        await asyncio.sleep(interval_seconds)


@router.get("/trend")
async def onboarding_funnel_trend(
    days: int = Query(30, ge=1, le=365),
):
    """Daily snapshot history for the trend line. Returns whatever
    history exists — if the snapshot cron has only run once, this
    correctly returns 1 point (no fabricated history)."""
    db = require_db()
    cutoff = (datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    docs = await db[SNAPSHOT_COLLECTION].find(
        {}, {"_id": 0},
    ).sort("date", 1).to_list(length=400)
    docs = [d for d in docs if d.get("date")]
    docs = docs[-days:] if len(docs) > days else docs
    # Always guarantee today's point exists in the response even if the
    # cron hasn't ticked yet today — compute it on the fly (read-only,
    # doesn't write) so the chart never looks stale on first load.
    if not docs or docs[-1].get("date") != cutoff:
        buckets = await classify_users(db)
        stage_counts = {key: len(buckets.get(key, [])) for key in STAGE_ORDER}
        docs.append({
            "date": cutoff, "generated_at": datetime.now(timezone.utc).isoformat(),
            "stages": stage_counts, "total_stuck": sum(stage_counts.values()),
        })
    return {"days_requested": days, "points": docs}


@router.get("/casl-screen")
async def onboarding_funnel_casl_screen(
    stage: Optional[str] = Query(
        None, description="Limit to one stage, or omit for the full stuck segment"),
):
    """Read-only CASL implied-consent expiry gate (2026-09-22 · Revenue
    Recovery audit). Runs the hard-coded date-based check from
    services/casl_consent.py against the current stuck-user segment —
    the same segment any future win-back/re-engagement list would be
    built from. MUST be run (and the excluded count reviewed) before
    that list is ever built; nobody past the CASL window is silently
    dropped, they're returned here with the reason and reference date.
    No email is sent by this endpoint."""
    db = require_db()
    buckets = await classify_users(db)
    if stage:
        if stage not in STAGES:
            raise HTTPException(400, f"unknown stage: {stage}")
        candidates = buckets.get(stage, [])
    else:
        candidates = [u for users in buckets.values() for u in users]
    result = await casl_screen(db, candidates)
    return {"stage_filter": stage or "all_stuck_stages", **result}


@router.get("/users")
async def onboarding_funnel_users(
    stage: str = Query(..., description="One of: " + ", ".join(STAGES)),
):
    """Per-user list for ONE stage — email, how long they've been
    stuck, and whether a nudge email has already been sent/clicked.
    Sorted most-stuck-first so the oldest cases surface at the top."""
    if stage not in STAGES:
        raise HTTPException(400, f"unknown stage: {stage}")
    db = require_db()
    buckets = await classify_users(db)
    users = buckets.get(stage, [])
    now_epoch = datetime.now(timezone.utc).timestamp()

    ids = [u["user_id"] for u in users if u.get("user_id")]
    nudge_by_user: dict[str, dict] = {}
    if ids:
        cur = db.onboarding_emails.find(
            {"user_id": {"$in": ids}, "campaign": "funnel_stage_nudge", "stage": stage},
            {"_id": 0, "user_id": 1, "sent_at": 1, "sent_ok": 1, "clicked_at": 1},
        )
        async for row in cur:
            uid = row.get("user_id")
            if not uid:
                continue
            prev = nudge_by_user.get(uid)
            sent_at = row.get("sent_at")
            sent_at = sent_at.timestamp() if hasattr(sent_at, "timestamp") else sent_at
            if prev is None or (sent_at or 0) > (prev.get("sent_at") or 0):
                clicked_at = row.get("clicked_at")
                clicked_at = clicked_at.timestamp() if hasattr(clicked_at, "timestamp") else clicked_at
                nudge_by_user[uid] = {
                    "sent_at": sent_at, "sent_ok": bool(row.get("sent_ok")),
                    "clicked_at": clicked_at,
                }

    rows = []
    for u in users:
        uid = u.get("user_id")
        stuck_since = u.get("_stuck_since")
        nudge = nudge_by_user.get(uid, {})
        rows.append({
            "user_id":       uid,
            "email":         u.get("email"),
            "name":          u.get("name"),
            "stuck_since":   _iso(stuck_since),
            "stuck_days":    _stuck_days(stuck_since, now_epoch),
            "nudge_sent":    bool(nudge),
            "nudge_sent_at": _iso(nudge.get("sent_at")),
            "nudge_clicked": bool(nudge.get("clicked_at")),
        })
    rows.sort(key=lambda r: r["stuck_days"] or 0, reverse=True)

    return {
        "stage": stage,
        "label": STAGE_META[stage]["label"],
        "count": len(rows),
        "users": rows,
    }
