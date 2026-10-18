"""
services/usage.py — Token usage aggregation + plan-limit enforcement.

Single source of truth for "how much has this user burned" and
"what's their effective ceiling".

  effective_limit = PLAN_LIMITS[user.tier]  +  user.tokens_granted
  used            = sum(cto_tasks.tokens_used where user_id=X, status=done)
                     + sum(real_llm_usage.total_tokens where user_id=X)
  remaining       = effective_limit - used

FOUNDER TIER (Iter 30): users with `tier == "founder"` or `is_unlimited == True`
NEVER hit a token ceiling — every call to `assert_has_budget` short-circuits to
"OK", and the UI reports an infinite remaining balance via the `is_unlimited`
flag. This is for the company's own founder accounts (e.g. teji.ss1986@gmail.com)
so internal usage doesn't consume customer-facing quota.

2026-09-27 — P0 fix (per-user usage tracking gap) + product decision to
DECOUPLE the token meter from customer-facing gating:
  • `used` now ALSO sums `real_llm_usage` — a new, always-on ledger
    (services.loop_token_ledger.log_real_usage) fed by every real
    OpenRouter call's own reported `usage` object, not just the narrow
    legacy `cto_tasks.tokens_used` writer. This is the actual fix for
    the "Used: 0" bug — the legacy writer only covered one call path.
  • `is_blocked` no longer factors in `is_exhausted` (token count).
    The ONLY customer-facing gate is the flat-fee MONTHLY_TASK_LIMITS
    task-count cap (see `assert_has_task_budget`) — this was already
    the documented pricing model (see module docstring above this
    note), the token counter just hadn't been decoupled from it yet.
  • `assert_has_budget` no longer raises HTTP 402. It still computes
    `is_exhausted` and LOGS a warning when a user crosses their old
    token ceiling (so we keep visibility into disproportionate token
    burn vs. plan), but never blocks the request. Tracking is kept;
    gating is removed. Task-count enforcement (`assert_has_task_budget`)
    is untouched.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from fastapi import HTTPException

from cto_services.db import require_db

logger = logging.getLogger(__name__)

from services.subscription_tiers import get_limit as _tier_limit
from config.quota_limits import (
    FREE_LIFETIME_BUDGET_USD, FREE_LIFETIME_TOKEN_DISPLAY, FIRST_SCAN_EXEMPT,
    PER_USER_DAILY_SPEND_CAP_USD,
)

PLAN_LIMITS = {
    # Q1 (2026-02) — derived from the single USD source of truth in
    # config/quota_limits.py (was a hardcoded 1_000 — see that module's
    # docstring for the evidence/justification). Do NOT hardcode a
    # token number here again; change FREE_LIFETIME_BUDGET_USD instead.
    "free":    FREE_LIFETIME_TOKEN_DISPLAY,
    "starter": 10_000,
    "pro":     50_000,
    "team":    100_000,
    # Founder plan — practically unlimited. The check in `assert_has_budget`
    # short-circuits before this value is ever compared, but we keep a huge
    # sentinel so any code path that reads `effective_limit` does the right
    # thing (e.g. UI shows "∞" / huge number).
    "founder": 1_000_000_000,
}

# Monthly task-count limits (the headline pricing model — flat-fee, no
# token surprises).  Single source of truth lives in
# services/subscription_tiers.py; this thin shim keeps the old import
# path stable for everything that already imports MONTHLY_TASK_LIMITS.
MONTHLY_TASK_LIMITS = {
    "free":    _tier_limit("free",    "tasks_per_month"),
    "starter": _tier_limit("starter", "tasks_per_month"),
    "pro":     _tier_limit("pro",     "tasks_per_month"),
    "team":    _tier_limit("team",    "tasks_per_month"),
    "founder": _tier_limit("founder", "tasks_per_month"),
}

# ── Iter 94: Maxx-mode (Claude Sonnet 4.5) monthly cap per tier ────────
# Maxx mode is the expensive code/review path. Pro tier gets 100/mo to
# protect margin against power-user abuse (see FOUNDER_LAUNCH_CHECKLIST).
# Team / Founder are uncapped. Free / Starter have no Maxx at all.
MAXX_MONTHLY_LIMITS = {
    "free":    _tier_limit("free",    "maxx_tasks_per_month"),
    "starter": _tier_limit("starter", "maxx_tasks_per_month"),
    "pro":     _tier_limit("pro",     "maxx_tasks_per_month"),
    "team":    _tier_limit("team",    "maxx_tasks_per_month"),
    "founder": _tier_limit("founder", "maxx_tasks_per_month"),
}



# Founder allow-list: addresses here auto-promote to tier="founder" +
# is_admin=true on next login. Stored in env so we can hot-rotate without
# a deploy. Hardcoded fallback for the company founder so the system always
# recognises them even if env was forgotten in a redeploy.
_DEFAULT_FOUNDERS = {"teji.ss1986@gmail.com"}


def founder_emails() -> set[str]:
    raw = os.environ.get("FOUNDER_EMAILS", "")
    extra = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return _DEFAULT_FOUNDERS | extra


def is_founder_email(email: str | None) -> bool:
    return bool(email) and email.lower().strip() in founder_emails()


async def get_usage(user_id: str) -> dict:
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id},
        {"tier": 1, "tokens_granted": 1, "is_unlimited": 1, "email": 1},
    )
    if not user:
        raise HTTPException(404, "User not found")

    email = user.get("email")
    tier = user.get("tier", "free")
    # Defensive: email-based founder check wins over a stale tier value
    if is_founder_email(email) or tier == "founder" or user.get("is_unlimited"):
        tier = "founder"

    granted = int(user.get("tokens_granted") or 0)
    plan_limit = PLAN_LIMITS.get(tier, PLAN_LIMITS["free"])
    effective = plan_limit + granted

    agg = await db.cto_tasks.aggregate([
        {"$match": {"user_id": user_id, "status": "done"}},
        {"$group": {"_id": None, "total": {"$sum": "$tokens_used"}}},
    ]).to_list(1)
    legacy_used = int(agg[0]["total"]) if agg else 0
    # 2026-09-27 P0 fix — the comprehensive real-usage ledger. Every
    # real OpenRouter call (chat, loop, scan — everything, not just
    # the narrow cto_tasks path above) writes here with its OWN
    # provider-reported token counts. Summed alongside the legacy
    # aggregate so we don't regress whatever that path already
    # counted, while the gap that made "Used" read ~0 gets closed.
    real_agg = await db.real_llm_usage.aggregate([
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": None, "total": {"$sum": "$total_tokens"},
                    "cost_usd": {"$sum": "$cost_usd"}}},
    ]).to_list(1)
    real_used = int(real_agg[0]["total"]) if real_agg else 0
    used = legacy_used + real_used
    # Q1 (2026-02) — the REAL gate for free tier is dollars, not
    # tokens (a cheap model's 50k tokens and an expensive model's 500
    # tokens are NOT the same cost — a token-only cap can't tell them
    # apart). `real_llm_usage.cost_usd` is the actual per-call cost
    # every LLM transport already logs (services/loop_token_ledger.py)
    # — summed here as the single source of truth for USD spend.
    used_usd = round(float(real_agg[0].get("cost_usd") or 0.0), 6) if real_agg else 0.0

    # Founders are never exhausted — we still surface a usage number so
    # they can see their own burn, but `is_exhausted` stays False forever.
    is_unlimited = tier == "founder"
    remaining = max(0, effective - used) if not is_unlimited else 10**12
    pct = round((used / effective) * 100, 1) if (effective > 0 and not is_unlimited) else 0
    is_exhausted = False if is_unlimited else (used >= effective)
    # Q1 — free-tier lifetime USD cap (see config/quota_limits.py).
    # Granted bonus tokens convert 1:1 into bonus USD budget via the
    # same display rate used to derive FREE_LIFETIME_TOKEN_DISPLAY, so
    # an admin "Grant tokens" action still means something real here.
    from config.quota_limits import TOKENS_PER_USD_DISPLAY
    effective_limit_usd = FREE_LIFETIME_BUDGET_USD + (granted / TOKENS_PER_USD_DISPLAY)
    is_exhausted_usd = (
        False if is_unlimited or tier != "free" else (used_usd >= effective_limit_usd)
    )

    # ── Monthly task counter (flat-fee meter). Counts every task this
    # user has SUBMITTED + ran since the first of the current UTC month.
    # Failed tasks are excluded (Iter 52 BUG 3) — a stale PAT or auth
    # error shouldn't burn the user's quota before the AI ran.
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0,
    )
    tasks_this_month = await db.cto_tasks.count_documents({
        "user_id": user_id,
        "created_at": {"$gte": month_start.timestamp()},
        "status": {"$in": ["done", "running", "pulling", "reading",
                            "fixing", "pushing", "queued"]},
    })
    # Iter 212m-190 — Developer-Tool scan fixes each count as 1 task
    # (see services/scan_fix_quota.py). Recorded per SUCCESSFUL fix.
    _sf = await db.scan_fix_usage.find_one(
        {"user_id": user_id,
         "month": f"{month_start.year:04d}-{month_start.month:02d}"},
        {"_id": 0, "count": 1},
    ) or {}
    tasks_this_month += int(_sf.get("count") or 0)
    # Iter 388n — Bug 6+7 fix.  Loop runs live in `loop_sessions`,
    # NOT in `cto_tasks`.  Before this patch, a founder who ran five
    # successful Plan→Ship loops in a month saw "Tasks this month: 0"
    # on Settings, "Success rate: 0%" on Analytics, and "0 steps" on
    # Ops History — because the counter was ignoring the collection
    # loops actually write to.  Now count `loop_sessions` where the
    # session reached COMPLETED (successful ship) OR is currently
    # running (matches the semantics of the cto_tasks query above).
    # Note: `loop_sessions.created_at` is a datetime (not a float
    # timestamp), so we pass `month_start` directly here.
    loop_runs_this_month = await db.loop_sessions.count_documents({
        "user_id": user_id,
        "created_at": {"$gte": month_start},
        "state": {"$in": [
            "completed", "shipping", "executing", "verifying",
            "scanning", "planning", "self_healing",
            "awaiting_confirmation", "paused_for_user",
        ]},
    })
    tasks_this_month += loop_runs_this_month
    # Iter 2026-09-12 — Visual Bug-Fix pipeline. A completed run (vision
    # + patch generation) counts as ONE task, same as any other real
    # edit — counted the moment a patch is successfully synthesized
    # (not merely uploaded/analyzed), mirroring the scan_fix_usage /
    # loop_sessions precedent just above instead of a new counter field.
    visual_bugfix_this_month = await db.visual_bug_reports.count_documents({
        "user_id": user_id,
        "created_at": {"$gte": month_start},
        "status": {"$in": ["patch_generated", "previewed", "preview_failed", "pr_created"]},
    })
    tasks_this_month += visual_bugfix_this_month
    task_cap = MONTHLY_TASK_LIMITS.get(tier, MONTHLY_TASK_LIMITS["free"])

    return {
        "user_id": user_id,
        "tier": tier,
        "plan_limit": plan_limit,
        "tokens_granted": granted,
        "effective_limit": effective,
        "used": used,
        "remaining": remaining,
        "pct_used": pct,
        "is_exhausted": is_exhausted,
        "is_unlimited": is_unlimited,
        "tasks_this_month": tasks_this_month,
        "monthly_task_cap": task_cap,
        "tasks_remaining":  None if task_cap is None
                            else max(0, task_cap - tasks_this_month),
        # Iter 364 · Phase 3 — canonical "actually blocked" flag for
        # the frontend banner. TRUE only when the server WILL refuse
        # a fresh chat/send call. Founders/unlimited never blocked.
        #
        # 2026-09-27 — Decoupled from token exhaustion (`is_exhausted`)
        # per product decision: the token counter is now an accurate
        # internal cost-visibility meter only, not a customer-facing
        # gate. The ONLY thing that blocks a real request is the
        # flat-fee monthly task-count cap — exactly what
        # `assert_has_task_budget` enforces below. `assert_has_budget`
        # (token gate) never raises anymore; keep this flag in sync
        # with that so the banner can't drift from what the server
        # will actually do.
        "is_blocked": bool(
            (not is_unlimited)
            and (task_cap is not None and tasks_this_month >= task_cap)
        ),
        # Q1 (2026-02) — free-tier lifetime USD budget (see
        # config/quota_limits.py). Only meaningful for tier=="free";
        # other tiers keep their existing token/task-based limits.
        "used_usd": used_usd,
        "effective_limit_usd": round(effective_limit_usd, 6),
        "is_exhausted_usd": is_exhausted_usd,
    }


async def assert_scan_budget(user_id: str, *, is_account_first_scan: bool) -> None:
    """Q1 (2026-02) — the ONLY budget gate a codebase-health scan goes
    through (PHASE 0 finding: previously there was none at all — see
    config/quota_limits.py docstring for the full evidence trail).

    Raises HTTP 402 with an honest "you've used your free allowance"
    message + fires an in-app nudge (once, lifetime-deduped — this is
    a lifetime budget, not monthly) when a free-tier user's REAL
    dollar spend has reached FREE_LIFETIME_BUDGET_USD. Never implies
    the task is still running — the caller must surface this as a
    terminal state, not a loading state.

    The account's first-ever scan (across ALL projects) is EXEMPT —
    see config/quota_limits.py::FIRST_SCAN_EXEMPT for the justification.
    Other tiers (starter/pro/team/founder) are untouched by this gate.
    """
    u = await get_usage(user_id)
    if u.get("is_unlimited") or u.get("tier") != "free":
        return
    if is_account_first_scan and FIRST_SCAN_EXEMPT:
        logger.info(
            "[Q1] first-ever scan exempted from lifetime budget "
            "user_id=%s used_usd=%.4f cap_usd=%.2f",
            user_id, u["used_usd"], u["effective_limit_usd"],
        )
        return
    if not u["is_exhausted_usd"]:
        return
    try:
        db = require_db()
        from services.notifications import emit_notification
        dedupe_key = f"free_allowance_exhausted:{user_id}"
        existing = await db.notif_dedupe.find_one({"_id": dedupe_key})
        if not existing:
            await db.notif_dedupe.insert_one({"_id": dedupe_key, "ts": datetime.now(timezone.utc)})
            await emit_notification(
                db, user_id=user_id, type="free_allowance_exhausted",
                text="You've used your free allowance. Upgrade to keep scanning and fixing.",
            )
    except Exception:                                       # noqa: BLE001
        pass  # never let a notification bug block the real 402 below
    raise HTTPException(402, detail={
        "error": "free_allowance_exhausted",
        "used_usd": u["used_usd"],
        "limit_usd": u["effective_limit_usd"],
        "upgrade_url": "/settings#pricing",
        "message": (
            "You've used your free allowance. Upgrade to keep scanning "
            "and fixing, or contact support if you think this is wrong."
        ),
    })


async def assert_daily_spend_cap(user_id: str) -> None:
    """Q2 (2026-02) — abuse guard, independent of the lifetime cap
    above. Protects against a single account hammering calls in a
    burst within one rolling 24h window — the lifetime cap alone
    wouldn't stop a free user from spending their whole $0.25 in one
    fast scripted burst; this adds a same-day ceiling on top. Honest
    402, never implies still-running.

    F1 defect 2 (2026-02) — this used to apply to EVERY non-founder
    tier, summing ALL real_llm_usage (chat/ORA included, not just
    scan). A paying user who chats a lot (a real 3-minute free
    session already costs $0.016 — easy to clear $0.10/day on
    legitimate paid usage) could get blocked from running a scan.
    Free tier only now — paying users aren't bound by this cap (the
    existing global $5/hr-$10/day breaker + Q2's platform alarm still
    cover platform-wide abuse regardless of tier).
    """
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id}, {"tier": 1, "is_unlimited": 1, "email": 1}) or {}
    tier = user.get("tier") or "free"
    if (user.get("is_unlimited") or tier == "founder"
            or is_founder_email(user.get("email")) or tier != "free"):
        return
    since = datetime.now(timezone.utc).timestamp() - 86400
    agg = await db.real_llm_usage.aggregate([
        {"$match": {"user_id": user_id, "created_at": {"$gte": since}}},
        {"$group": {"_id": None, "cost_usd": {"$sum": "$cost_usd"}}},
    ]).to_list(1)
    spent_today = round(float(agg[0].get("cost_usd") or 0.0), 6) if agg else 0.0
    if spent_today < PER_USER_DAILY_SPEND_CAP_USD:
        return
    try:
        from services.notifications import emit_notification
        dedupe_key = f"daily_spend_cap_hit:{user_id}:{datetime.now(timezone.utc).date().isoformat()}"
        existing = await db.notif_dedupe.find_one({"_id": dedupe_key})
        if not existing:
            await db.notif_dedupe.insert_one({"_id": dedupe_key, "ts": datetime.now(timezone.utc)})
            await emit_notification(
                db, user_id=user_id, type="daily_spend_cap_hit",
                text="You've hit today's usage cap. Try again tomorrow, or upgrade for a higher limit.",
            )
    except Exception:                                       # noqa: BLE001
        pass
    raise HTTPException(402, detail={
        "error": "daily_spend_cap_hit",
        "spent_today_usd": spent_today,
        "daily_cap_usd": PER_USER_DAILY_SPEND_CAP_USD,
        "message": (
            "You've hit today's usage cap. Try again tomorrow, or "
            "upgrade for a higher limit."
        ),
    })


async def assert_has_task_budget(user_id: str) -> None:
    """Raise HTTP 402 if the user has hit their monthly task cap.

    Flat-fee tiers (free=10, starter=50) have a hard ceiling. Pro / Team /
    Founder are unlimited and short-circuit immediately.
    """
    u = await get_usage(user_id)
    if u.get("is_unlimited"):
        return
    cap = u.get("monthly_task_cap")
    if cap is None:
        return
    if u.get("tasks_this_month", 0) >= cap:
        raise HTTPException(402, detail={
            "error": "monthly_task_limit_reached",
            "tier": u["tier"],
            "tasks_this_month": u["tasks_this_month"],
            "monthly_task_cap": cap,
            "upgrade_url": "/settings#pricing",
            "message": (
                f"You've used your {cap} free fixes this month on the "
                f"{u['tier'].title()} plan. Upgrade to Pro for unlimited "
                "fixes."
            ),
        })


async def assert_has_budget(user_id: str) -> None:
    """2026-09-27 — Decoupled (product decision, see module docstring).

    Historically raised HTTP 402 once `used >= effective_limit`. Now
    NEVER raises — the flat-fee MONTHLY_TASK_LIMITS task-count cap
    (`assert_has_task_budget`) is the sole customer-facing gate.
    Tracking is NOT deleted: this still computes real exhaustion state
    via `get_usage` and logs a warning when a user has crossed their
    old token ceiling, purely for founder visibility into which users
    burn disproportionate token cost vs. their plan. Never blocks.
    """
    u = await get_usage(user_id)
    if u.get("is_unlimited"):
        return  # Founder — never billed, never blocked.
    if u["is_exhausted"]:
        logger.warning(
            "TOKEN_BUDGET_WOULD_HAVE_BLOCKED user_id=%s tier=%s used=%s "
            "effective_limit=%s — gating disabled 2026-09-27, task-count "
            "cap is the only enforced limit; logged for cost visibility.",
            user_id, u["tier"], u["used"], u["effective_limit"],
        )


# ── Iter 94: Maxx-mode usage tracking ─────────────────────────────────
def _current_year_month() -> str:
    """Returns 'YYYY-MM' for current UTC time — the bucket key."""
    n = datetime.now(timezone.utc)
    return f"{n.year:04d}-{n.month:02d}"


async def get_maxx_usage(user_id: str) -> dict:
    """
    Return Maxx-mode (Claude) usage state for the current month.

    Output:
      tier             — user's tier
      cap              — int | None  (None = unlimited)
      used             — int (this month's Claude calls)
      remaining        — int | None
      capped           — bool (True iff used >= cap AND cap is not None)

    Free / Starter have cap=0 (never allowed). Pro=100. Team/Founder=None.
    """
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id},
        {"tier": 1, "email": 1, "is_unlimited": 1},
    )
    if not user:
        # Anonymous / unknown — treat as free.
        tier = "free"
    else:
        tier = user.get("tier", "free")
        if (is_founder_email(user.get("email"))
            or user.get("is_unlimited")
            or tier == "founder"):
            tier = "founder"

    cap = MAXX_MONTHLY_LIMITS.get(tier)
    # Unlimited (Team / Founder)
    if cap is None:
        return {"tier": tier, "cap": None, "used": 0,
                "remaining": None, "capped": False}

    bucket = _current_year_month()
    row = await db.cto_maxx_usage.find_one(
        {"user_id": user_id, "month": bucket},
        {"_id": 0, "count": 1, "overage_count": 1},
    ) or {}
    used = int(row.get("count") or 0)
    overage = int(row.get("overage_count") or 0)
    OVERAGE_PRICE = 0.50  # USD per Maxx task past cap (iter 101)
    return {
        "tier":             tier,
        "cap":              cap,
        "used":             used,
        "remaining":        max(0, cap - used),
        "capped":           used >= cap,
        "overage_count":    overage,
        "overage_cost_usd": round(overage * OVERAGE_PRICE, 2),
        "overage_price_usd": OVERAGE_PRICE,
    }


async def incr_maxx_usage(user_id: str) -> int:
    """Atomically bump this user's Maxx counter for the current month.
    Returns the new count. Iter 101 — also bumps `overage_count` when
    we're already past the cap (for end-of-month $0.50/task billing
    of Pro+ users)."""
    db = require_db()
    bucket = _current_year_month()
    # First, check if user is currently capped — that determines whether
    # this call is overage or included.
    pre = await get_maxx_usage(user_id)
    is_overage = bool(pre.get("capped"))
    inc = {"count": 1}
    if is_overage:
        inc["overage_count"] = 1
    res = await db.cto_maxx_usage.find_one_and_update(
        {"user_id": user_id, "month": bucket},
        {"$inc": inc,
         "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
        return_document=True,
    )
    return int((res or {}).get("count") or 1)

