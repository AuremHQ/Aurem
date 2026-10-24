"""
services/llm_cost_breaker.py — G13 · LLM cost circuit breaker (Iter 366)

Hourly + daily USD caps across ALL LLM providers. Cap hit → subsequent
calls raise HTTPException(429 llm_cost_cap_hit) + fire a G10 founder
alert (max 1/6h dedup).

Per-loop cap: any single loop_id crossing LLM_COST_CAP_PER_LOOP is
auto-killed and marked FAILED with resume_reason="llm_cost_cap".

Env:
  LLM_COST_CAP_HOURLY    default 2.00  ($)
  LLM_COST_CAP_DAILY     default 10.00 ($)
  LLM_COST_CAP_PER_LOOP  default 3.00  ($)

2026-08 hardening (F2 · B3) — per-loop cap raised 0.50 → 3.00. The cap's
job is to stop a RUNAWAY loop, not a normal complex task: a real Council
run (3 members + CEO, possibly several files + self-heal retries) can
legitimately land near $1 for a large task. $0.50 would trip on ordinary
work; $3.00 is well above any observed real loop cost (see Task-2 cost
audit — the one real Council loop cost ~$0.006) while still catching a
genuine runaway (e.g. a stuck retry storm).

Collections used:
  llm_cost_ledger — {ts, provider, cost_usd, user_id, loop_id, model}

Wiring: `record_cost(...)` is called from services/llm.py after every
call_llm returns with a `cost_usd` estimate. `assert_within_cap()` is
called BEFORE the call.
"""
from __future__ import annotations

import os
import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger("aurem.llm_cost_breaker")


def _envf(k: str, d: float) -> float:
    try:    return float(os.environ.get(k, str(d)))
    except (ValueError, TypeError): return d


LLM_COST_CAP_HOURLY   = _envf("LLM_COST_CAP_HOURLY",   5.00)
LLM_COST_CAP_DAILY    = _envf("LLM_COST_CAP_DAILY",   10.00)
LLM_COST_CAP_PER_LOOP = _envf("LLM_COST_CAP_PER_LOOP", 3.00)
# Master Prompt Step B9 — per-PAID-user monthly LLM cost cap. Separate
# from the platform-wide hourly/daily caps above (those protect the
# whole platform from a runaway burst; this protects margin on ONE
# user who is burning real LLM cost disproportionate to what they
# paid). Scoped to paid tiers only — a free-tier user is already
# capped by services/usage.py's lifetime USD budget.
LLM_COST_CAP_PER_USER_MONTHLY = _envf("LLM_COST_CAP_PER_USER_MONTHLY", 6.50)


async def _sum_cost_since(db, since: datetime,
                           loop_id: Optional[str] = None) -> float:
    if db is None:
        return 0.0
    match: dict = {"ts": {"$gte": since}}
    if loop_id:
        match["loop_id"] = loop_id
    try:
        pipeline = [
            {"$match": match},
            {"$group": {"_id": None, "sum": {"$sum": "$cost_usd"}}},
        ]
        async for row in db.llm_cost_ledger.aggregate(pipeline):
            return float(row.get("sum") or 0.0)
    except Exception as e:
        logger.debug("[G13] sum_cost query failed: %r", e)
    return 0.0


async def assert_within_cap(
    db, *, loop_id: Optional[str] = None,
    est_cost_usd: float = 0.0,
) -> None:
    """Raise 429 if the projected spend (current spend + estimated cost
    of THIS call) would exceed any cap."""
    if db is None:
        return
    now  = datetime.now(timezone.utc)
    h1   = now - timedelta(hours=1)
    d1   = now - timedelta(days=1)
    spend_h = await _sum_cost_since(db, h1)
    spend_d = await _sum_cost_since(db, d1)

    cost = float(est_cost_usd or 0.0)
    # 2026-08 hardening (F2 · B3) — check the per-loop cap FIRST, before
    # hourly/daily. Raising the per-loop cap (0.50 → 3.00) put it ABOVE
    # the (pre-existing) hourly cap; if hourly were checked first, a
    # single loop's own spend (which also counts toward the global
    # hourly sum) would always trip the generic hourly message before
    # the specific, actionable per-loop budget message ever fired.
    # Checking per-loop first keeps the message correctly scoped to
    # "this loop's budget", not a vague org-wide cap.
    if loop_id:
        spend_l = await _sum_cost_since(db, h1 - timedelta(hours=23),
                                          loop_id=loop_id)
        if spend_l + cost > LLM_COST_CAP_PER_LOOP:
            # Signal loop_engine to auto-kill this loop.
            try:
                await db.loop_sessions.update_one(
                    {"loop_id": loop_id},
                    {"$set": {"resume_reason": "llm_cost_cap"}},
                )
            except Exception:
                pass
            raise HTTPException(429, {
                "error": "llm_cost_cap_hit",
                # 2026-08 hardening (F2) — distinguishable code so the
                # _meta.py translation layer + loop_engine.py's additive
                # pause-check can tell "budget exhausted" apart from a
                # timeout (retry) or a genuine error (fail). Do NOT
                # rename without updating loop_engine.py's check.
                "error_code": "COST_CAP_REACHED",
                "cap":   "per_loop", "limit": LLM_COST_CAP_PER_LOOP,
                "loop_id":  loop_id,
                "loop_spend_usd": round(spend_l, 4),
                "message": ("You've used up your tasks for this month. "
                            "Your work is safe."),
            })
    if spend_h + cost > LLM_COST_CAP_HOURLY:
        await _fire_cap_alert(db, "hourly", spend_h, LLM_COST_CAP_HOURLY)
        raise HTTPException(429, {
            "error": "llm_cost_cap_hit",
            "error_code": "COST_CAP_HOURLY",
            "cap":   "hourly", "limit": LLM_COST_CAP_HOURLY,
            "spent_last_hour": round(spend_h, 4),
            "message": ("Hourly LLM spend cap reached — new requests "
                        "temporarily blocked. Retry in ~1h."),
        })
    if spend_d + cost > LLM_COST_CAP_DAILY:
        await _fire_cap_alert(db, "daily", spend_d, LLM_COST_CAP_DAILY)
        raise HTTPException(429, {
            "error": "llm_cost_cap_hit",
            "error_code": "COST_CAP_DAILY",
            "cap":   "daily", "limit": LLM_COST_CAP_DAILY,
            "spent_last_24h": round(spend_d, 4),
            "message": ("Daily LLM spend cap reached — new requests "
                        "blocked until UTC midnight."),
        })


async def assert_within_user_monthly_cap(
    db, *, user_id: str, est_cost_usd: float = 0.0,
) -> None:
    """Master Prompt Step B9. Raise 429 if THIS user's real LLM spend
    this calendar month (`real_llm_usage.cost_usd`, the same ledger
    services/usage.py reads) plus the projected cost of this call
    would exceed `LLM_COST_CAP_PER_USER_MONTHLY`. Scoped to paid tiers
    only — callers should check tier before calling this (mirrors
    services/usage.py::assert_daily_spend_cap's free-tier-only scoping
    in the opposite direction)."""
    if db is None or not user_id:
        return
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    try:
        pipeline = [
            {"$match": {"user_id": user_id,
                         "ts": {"$gte": month_start.timestamp()}}},
            {"$group": {"_id": None, "sum": {"$sum": "$cost_usd"}}},
        ]
        spent = 0.0
        async for row in db.real_llm_usage.aggregate(pipeline):
            spent = float(row.get("sum") or 0.0)
    except Exception as e:                                   # noqa: BLE001
        logger.debug("[G13/B9] user-monthly cost sum failed: %r", e)
        return
    cost = float(est_cost_usd or 0.0)
    if spent + cost <= LLM_COST_CAP_PER_USER_MONTHLY:
        return
    raise HTTPException(429, {
        "error": "llm_cost_cap_hit",
        "error_code": "COST_CAP_USER_MONTHLY",
        "cap": "per_user_monthly", "limit": LLM_COST_CAP_PER_USER_MONTHLY,
        "spent_this_month_usd": round(spent, 4),
        "message": ("You've reached this month's usage cap for your plan. "
                     "It resets at the start of next month, or top up now."),
    })


async def record_cost(
    db, *,
    provider:    str,
    cost_usd:    float,
    user_id:     Optional[str] = None,
    loop_id:     Optional[str] = None,
    model:       Optional[str] = None,
) -> None:
    """Best-effort — never raises. Called AFTER every LLM call returns."""
    if db is None or cost_usd is None:
        return
    try:
        await db.llm_cost_ledger.insert_one({
            "ts":       datetime.now(timezone.utc),
            "provider": provider,
            "cost_usd": float(cost_usd or 0.0),
            "user_id":  user_id,
            "loop_id":  loop_id,
            "model":    model,
        })
    except Exception as e:
        logger.debug("[G13] record_cost write failed: %r", e)


async def flag_actual_cost_spike(
    db, *, user_id: Optional[str], action: str, run_id: str,
    estimated_cost_usd: float, actual_cost_usd: float,
) -> None:
    """Master Prompt v2 Part 2 — called by services.wallet.settle() when
    a run's real cost comes in at >= SPIKE_GUARD_MULTIPLE times the held
    estimate. This is a PLATFORM cost anomaly signal (a single run got
    unexpectedly expensive — stuck retry loop, runaway tool use, a
    pricing-table estimate that's badly out of date), not a per-call cap
    like assert_within_cap above. DB-flag only (services.billing_flags —
    same no-email admin-review pattern as Part 1.3's refund/dispute
    flags), never an email; the user's own charge is already capped by
    wallet.settle() before this is called. Never raises.
    `idempotency_key` is derived from `run_id`, which is unique per run,
    so a retried settle() for the same run never double-flags."""
    try:
        from services import billing_flags
        await billing_flags.flag(
            db, flag_type="cost_spike", user_id=user_id or "unknown",
            idempotency_key=f"cost_spike:{run_id}",
            detail={
                "action": action, "run_id": run_id,
                "estimated_cost_usd": round(estimated_cost_usd, 6),
                "actual_cost_usd": round(actual_cost_usd, 6),
                "multiple": round(actual_cost_usd / max(estimated_cost_usd, 0.0001), 2),
            },
        )
        logger.warning(
            "[pricing_engine] cost spike: action=%s run_id=%s actual=$%.4f "
            "estimated=$%.4f (%.1fx) — flagged for admin review",
            action, run_id, actual_cost_usd, estimated_cost_usd,
            actual_cost_usd / max(estimated_cost_usd, 0.0001),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[pricing_engine] flag_actual_cost_spike failed: %r", e)


async def _fire_cap_alert(db, cap_kind: str, spend: float, cap: float) -> None:
    try:
        from services.founder_alerts import send_founder_alert
        await send_founder_alert(
            db,
            source_key=f"llm_cost_cap:{cap_kind}",
            title=f"LLM {cap_kind} spend cap hit (${spend:.2f} / ${cap:.2f})",
            detail=(f"The {cap_kind} LLM spend cap has been reached. "
                    f"New calls are being rejected with HTTP 429 until "
                    f"the window rolls over. Verify no runaway loop / "
                    f"malicious traffic before raising the cap."),
            level="critical", guard="G13",
        )
    except Exception:
        pass


async def spend_summary(db) -> dict:
    """QA panel snapshot — current spend vs each cap."""
    if db is None:
        return {"available": False}
    now = datetime.now(timezone.utc)
    return {
        "available":         True,
        "hourly_spent":      round(await _sum_cost_since(db, now - timedelta(hours=1)), 4),
        "hourly_cap":        LLM_COST_CAP_HOURLY,
        "daily_spent":       round(await _sum_cost_since(db, now - timedelta(days=1)), 4),
        "daily_cap":         LLM_COST_CAP_DAILY,
        "per_loop_cap":      LLM_COST_CAP_PER_LOOP,
    }
