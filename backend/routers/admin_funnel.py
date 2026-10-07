"""
routers/admin_funnel.py — 2026-09-28 MASTER TASK "ACTIVATION FIRST" Part 1

Permanent admin/founder-only, READ-ONLY funnel dashboard:
GET /admin/funnel?start=<ISO date>&end=<ISO date>

Computes, from real event/collection data (never guesses), for every
non-test signup in the date range:
    signed up -> opened chat -> connected repo OR skipped ->
    sent first message -> first task started -> first task completed ->
    came back another day

...plus WHY users dropped between "sent first message" and "first task
completed" (classified from provable signals only — anything that can't
be proven from logs is bucketed "unknown", never guessed), and the
median/p90 token cost of a first completed task, with vs without a
connected repo at the time the task started.

No writes. No bulk email export. Every query is scoped read-only.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from cto_services.auth import require_admin_dep
from cto_services.db import require_db
from routers._admin_common import _require_admin
from routers.admin import _epoch
from services.test_accounts import is_test_email as is_test

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin", tags=["Admin-funnel"],
    dependencies=[Depends(require_admin_dep)],
)

FUNNEL_STEPS = [
    ("signed_up",              "Signed up"),
    ("opened_chat",            "Opened chat"),
    ("repo_or_skipped",        "Connected repo or skipped"),
    ("sent_first_message",     "Sent first message"),
    ("first_task_started",     "First task started"),
    ("first_task_completed",   "First task completed"),
    ("came_back_another_day",  "Came back another day"),
]

_EVENT_TYPES = [
    "chat_opened", "repo_connect_gate_shown", "repo_connect_gate_skipped",
    "repo_connect_gate_completed", "project_connected", "first_chat_sent",
    "task_submitted", "first_task_shipped", "first_scan_started",
    "first_scan_completed",
    # 2026-09-28 "ACTIVATION FIRST" Part 3 — the repo-LESS website
    # scan (routers/onboarding.py::scan_website) uses its own event
    # names, distinct from first_scan_*  which requires a project_id.
    "website_scan_started", "website_scan_completed",
    "demo_project_started", "demo_project_completed",
]

DROP_REASONS = [
    "scan_failed",            # first_scan_started, never first_scan_completed
    "llm_error_or_timeout",   # a real_llm_usage row with status != ok, or a
                               # persisted turn_state == "error"
    "monthly_task_limit_hit", # tasks_this_month >= plan cap
    "no_repo_connected",      # skipped the repo gate, never got a project,
                               # never started a task
    "low_confidence_or_refused",  # ORA's own bail_reason fired on their turns
    "unknown",                 # can't be proven from logs — never guessed
]


def _parse_range(start: Optional[str], end: Optional[str]) -> tuple[float, float]:
    now = datetime.now(timezone.utc)
    try:
        end_dt = datetime.fromisoformat(end) if end else now
    except ValueError:
        raise HTTPException(400, "end must be an ISO date")
    try:
        start_dt = datetime.fromisoformat(start) if start else (end_dt - timedelta(days=30))
    except ValueError:
        raise HTTPException(400, "start must be an ISO date")
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return start_dt.timestamp(), end_dt.timestamp()


def _day(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


@router.get("/funnel/activation")
async def get_activation_funnel(
    start: Optional[str] = None,
    end: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    start_ts, end_ts = _parse_range(start, end)

    # ── 1. Cohort: non-test signups in range ───────────────────────
    raw_users = await db.dev_users.find(
        {}, {"_id": 0, "user_id": 1, "email": 1, "created_at": 1,
             "tier": 1, "first_chat_at": 1, "first_ship_at": 1, "github": 1},
    ).to_list(20000)
    cohort = []
    for u in raw_users:
        if is_test(u.get("email")):
            continue
        ts = _epoch(u.get("created_at"))
        if ts is not None and start_ts <= ts <= end_ts:
            cohort.append(u)
    cohort_ids = [u["user_id"] for u in cohort if u.get("user_id")]
    cohort_size = len(cohort)

    if cohort_size == 0:
        return {
            "ok": True, "range": {"start": _day(start_ts), "end": _day(end_ts)},
            "cohort_size": 0, "funnel": [
                {"step": s, "label": l, "count": 0, "pct": 0.0} for s, l in FUNNEL_STEPS
            ],
            "drop_reasons": {"cohort": 0, "reasons": []},
            "first_task_tokens": {"with_repo": None, "without_repo": None},
            "note": "No non-test signups in this date range.",
        }

    # ── 2. Earliest funnel_events per (user, event_type) ────────────
    events: dict[str, dict[str, float]] = {uid: {} for uid in cohort_ids}
    cur = db.funnel_events.find(
        {"user_id": {"$in": cohort_ids}, "event_type": {"$in": _EVENT_TYPES}},
        {"_id": 0, "user_id": 1, "event_type": 1, "ts_epoch": 1, "created_at": 1},
    )
    async for ev in cur:
        uid, et = ev.get("user_id"), ev.get("event_type")
        ts = ev.get("ts_epoch") or _epoch(ev.get("created_at"))
        if uid not in events or ts is None:
            continue
        if et not in events[uid] or ts < events[uid][et]:
            events[uid][et] = ts

    # ── 3. Fallback signals already on dev_users (older/other paths) ─
    project_reached: dict[str, float] = {}
    cur = db.cto_projects.find(
        {"user_id": {"$in": cohort_ids}}, {"_id": 0, "user_id": 1, "created_at": 1},
    )
    async for p in cur:
        uid, ts = p.get("user_id"), _epoch(p.get("created_at"))
        if uid and ts and (uid not in project_reached or ts < project_reached[uid]):
            project_reached[uid] = ts

    # ── 4. "Came back another day" — any activity on a LATER calendar
    #      day than signup, from chat_sessions / cto_tasks / funnel_events.
    signup_day: dict[str, str] = {u["user_id"]: _day(_epoch(u.get("created_at"))) for u in cohort}
    returned: set[str] = set()
    cur = db.chat_sessions.find(
        {"user_id": {"$in": cohort_ids}}, {"_id": 0, "user_id": 1, "updated_at": 1, "created_at": 1},
    )
    async for s in cur:
        uid = s.get("user_id")
        for f in ("updated_at", "created_at"):
            d = _day(_epoch(s.get(f)))
            if uid and d and signup_day.get(uid) and d > signup_day[uid]:
                returned.add(uid)
    cur = db.cto_tasks.find(
        {"user_id": {"$in": cohort_ids}}, {"_id": 0, "user_id": 1, "created_at": 1},
    )
    async for t in cur:
        uid, d = t.get("user_id"), _day(_epoch(t.get("created_at")))
        if uid and d and signup_day.get(uid) and d > signup_day[uid]:
            returned.add(uid)
    for uid, ev in events.items():
        sd = signup_day.get(uid)
        if not sd:
            continue
        for ts in ev.values():
            if _day(ts) and _day(ts) > sd:
                returned.add(uid)
                break

    # ── 5. Per-user step reach + drop-reason inputs ──────────────────
    monthly_caps = {"free": 10, "starter": 50}
    error_user_ids: set[str] = set()
    cur = db.real_llm_usage.find(
        {"user_id": {"$in": cohort_ids}, "status": {"$nin": [None, "ok", "success"]}},
        {"_id": 0, "user_id": 1},
    )
    async for r in cur:
        if r.get("user_id"):
            error_user_ids.add(r["user_id"])
    low_conf_user_ids: set[str] = set()
    cur = db.chat_sessions.find(
        {"user_id": {"$in": cohort_ids}, "turns.low_confidence": True},
        {"_id": 0, "user_id": 1},
    )
    async for r in cur:
        if r.get("user_id"):
            low_conf_user_ids.add(r["user_id"])
    task_counts: dict[str, int] = {}
    cur = db.cto_tasks.aggregate([
        {"$match": {"user_id": {"$in": cohort_ids}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
    ])
    async for r in cur:
        task_counts[r["_id"]] = r.get("n", 0)

    step_counts = {s: 0 for s, _ in FUNNEL_STEPS}
    step_counts["signed_up"] = cohort_size
    drop_cohort = []       # users who sent a message but never completed
    reason_counts = {r: 0 for r in DROP_REASONS}
    task_windows = []      # (user_id, has_repo_at_start, started_ts, completed_ts)

    for u in cohort:
        uid = u["user_id"]
        ev = events.get(uid, {})
        opened = "chat_opened" in ev or bool(u.get("first_chat_at"))
        gate_touched = any(k in ev for k in
                            ("repo_connect_gate_completed", "repo_connect_gate_skipped",
                             "project_connected")) or uid in project_reached
        sent_msg = "first_chat_sent" in ev or bool(u.get("first_chat_at"))
        started = "task_submitted" in ev or "website_scan_started" in ev or "demo_project_started" in ev
        completed_ts = None
        if "first_task_shipped" in ev:
            completed_ts = ev["first_task_shipped"]
        elif "first_scan_completed" in ev:
            completed_ts = ev["first_scan_completed"]
        elif "website_scan_completed" in ev:
            completed_ts = ev["website_scan_completed"]
        elif "demo_project_completed" in ev:
            completed_ts = ev["demo_project_completed"]
        elif u.get("first_ship_at"):
            completed_ts = _epoch(u.get("first_ship_at"))
        completed = completed_ts is not None
        came_back = uid in returned

        if opened:              step_counts["opened_chat"] += 1
        if gate_touched:        step_counts["repo_or_skipped"] += 1
        if sent_msg:            step_counts["sent_first_message"] += 1
        if started:             step_counts["first_task_started"] += 1
        if completed:           step_counts["first_task_completed"] += 1
        if came_back:           step_counts["came_back_another_day"] += 1

        if sent_msg and not completed:
            drop_cohort.append(uid)
            has_repo = uid in project_reached
            if ("first_scan_started" in ev and "first_scan_completed" not in ev) or \
               ("website_scan_started" in ev and "website_scan_completed" not in ev) or \
               ("demo_project_started" in ev and "demo_project_completed" not in ev):
                reason = "scan_failed"
            elif uid in error_user_ids:
                reason = "llm_error_or_timeout"
            elif task_counts.get(uid, 0) >= monthly_caps.get(u.get("tier", "free"), 10 ** 9):
                reason = "monthly_task_limit_hit"
            elif "repo_connect_gate_skipped" in ev and not has_repo and not started:
                reason = "no_repo_connected"
            elif uid in low_conf_user_ids:
                reason = "low_confidence_or_refused"
            else:
                reason = "unknown"
            reason_counts[reason] += 1

        if completed and started:
            started_ts = ev.get("task_submitted")
            has_repo_at_start = (uid in project_reached and started_ts is not None
                                 and project_reached[uid] <= started_ts)
            task_windows.append((uid, has_repo_at_start, started_ts, completed_ts))

    # ── 6. First-task token cost, with vs without repo ───────────────
    def _percentiles(vals: list[float]) -> Optional[dict]:
        if not vals:
            return None
        vals = sorted(vals)
        n = len(vals)
        p90_idx = min(n - 1, int(round(0.9 * (n - 1))))
        return {
            "n": n,
            "median": round(statistics.median(vals), 0),
            "p90": round(vals[p90_idx], 0),
        }

    with_repo_tokens, without_repo_tokens = [], []
    for uid, has_repo, started_ts, completed_ts in task_windows:
        if started_ts is None or completed_ts is None:
            continue
        window_end = completed_ts + 300  # 5 min buffer for trailing calls
        total = 0
        cur = db.real_llm_usage.find(
            {"user_id": uid, "ts": {"$gte": started_ts - 5, "$lte": window_end}},
            {"_id": 0, "total_tokens": 1},
        )
        async for r in cur:
            total += r.get("total_tokens") or 0
        if total > 0:
            (with_repo_tokens if has_repo else without_repo_tokens).append(total)

    funnel = [
        {
            "step": s, "label": l, "count": step_counts[s],
            "pct": round(100.0 * step_counts[s] / cohort_size, 1) if cohort_size else 0.0,
        }
        for s, l in FUNNEL_STEPS
    ]

    return {
        "ok": True,
        "range": {"start": _day(start_ts), "end": _day(end_ts)},
        "cohort_size": cohort_size,
        "funnel": funnel,
        "drop_reasons": {
            "cohort": len(drop_cohort),
            "reasons": [
                {"reason": r, "count": reason_counts[r]} for r in DROP_REASONS
                if reason_counts[r] > 0 or r == "unknown"
            ],
        },
        "first_task_tokens": {
            "with_repo":    _percentiles(with_repo_tokens),
            "without_repo": _percentiles(without_repo_tokens),
        },
    }


@router.get("/funnel/checkup")
async def get_checkup_funnel(
    start: Optional[str] = None,
    end: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """Checkup-first fix funnel (see services/fix_items.FUNNEL_EVENTS)."""
    await _require_admin(authorization)
    db = require_db()
    start_ts, end_ts = _parse_range(start, end)
    from services.fix_items import funnel_summary
    out = await funnel_summary(
        db, start=datetime.fromtimestamp(start_ts, tz=timezone.utc),
        end=datetime.fromtimestamp(end_ts, tz=timezone.utc))
    return {"ok": True, "range": {"start": _day(start_ts), "end": _day(end_ts)}, **out}
