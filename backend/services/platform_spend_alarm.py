"""
services/platform_spend_alarm.py — Q2 (2026-02)

Platform-wide daily spend alarm. Separate from, and lower-friction
than, the existing hard circuit breaker in services/llm_cost_breaker.py
(which blocks new LLM calls with a 429 once LLM_COST_CAP_DAILY is hit).
This module ONLY alarms — it never blocks anything.

PHASE 0 / Q1 finding this respects: Resend is broken/unverified in
this environment, so this alarm does NOT depend on email delivery to
be useful. It writes directly to `platform_spend_alerts` (read by the
admin panel) and ALSO best-effort tries the existing founder-alert
email path in case Resend is actually fine by the time this runs —
but never silently relies on it.

Uses `real_llm_usage` — the single canonical cost ledger for the
whole app (services/loop_token_ledger.py, MASTER TASK Part A,
2026-09-27) — NOT the separate/older `llm_cost_ledger` the hard
breaker reads, so Q1's and Q2's dollar figures stay consistent with
each other.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from cto_services.db import require_db
from config.quota_limits import PLATFORM_DAILY_SPEND_ALARM_USD

logger = logging.getLogger(__name__)


async def check_and_alert(db=None) -> dict:
    """Sum real LLM spend across ALL users in the last 24h; if it
    crosses PLATFORM_DAILY_SPEND_ALARM_USD, write an admin-visible
    alert into the EXISTING `topup_alerts` collection (same one
    services/github_sync.py's Guard 8 already writes to, already
    surfaced by GET /admin/alerts — reusing it means this alarm is
    admin-visible immediately, no new endpoint/UI needed) + best-
    effort email. Deduped once per UTC day via `alert_key`. Safe to
    call frequently — never raises, a broken alarm must never break
    the real LLM call that triggered the check."""
    try:
        db = db or require_db()
        since = datetime.now(timezone.utc).timestamp() - 86400
        agg = await db.real_llm_usage.aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": None, "cost_usd": {"$sum": "$cost_usd"}}},
        ]).to_list(1)
        spend_24h = round(float(agg[0].get("cost_usd") or 0.0), 4) if agg else 0.0
        if spend_24h < PLATFORM_DAILY_SPEND_ALARM_USD:
            return {"alarmed": False, "spend_24h_usd": spend_24h}

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"platform_daily_spend::{day}"
        now = datetime.now(timezone.utc)
        summary = (f"Platform-wide LLM spend in the last 24h is ${spend_24h:.2f}, "
                   f"over the ${PLATFORM_DAILY_SPEND_ALARM_USD:.2f} alarm threshold.")
        existing = await db.topup_alerts.find_one({"alert_key": key}, {"_id": 1})
        if existing:
            await db.topup_alerts.update_one(
                {"alert_key": key},
                {"$set": {"last_seen": now, "status": "active", "summary": summary[:300]},
                 "$inc": {"seen_count": 1}},
            )
            return {"alarmed": False, "already_fired_today": True, "spend_24h_usd": spend_24h}
        await db.topup_alerts.insert_one({
            "alert_id": f"al_{uuid.uuid4().hex[:10]}",
            "alert_key": key,
            "integration_id": "platform_daily_spend",
            "integration_name": "Platform daily LLM spend (Q2)",
            "severity": "critical",
            "summary": summary[:300],
            "detail": summary,
            "fix_hint": "Check real_llm_usage for the top-spending users/models today.",
            "day_key": day,
            "first_seen": now, "last_seen": now, "seen_count": 1,
            "status": "active", "email_sent": False,
        })
        try:
            from services.founder_alerts import send_founder_alert
            await send_founder_alert(
                db, source_key="platform_daily_spend",
                title="Platform daily LLM spend alarm", detail=summary,
            )
        except Exception as e:                                  # noqa: BLE001
            logger.warning("[Q2] platform spend alarm: founder email best-effort failed: %r", e)
        logger.warning("[Q2] PLATFORM SPEND ALARM fired: $%.2f in 24h (threshold $%.2f)",
                        spend_24h, PLATFORM_DAILY_SPEND_ALARM_USD)
        return {"alarmed": True, "spend_24h_usd": spend_24h}
    except Exception as e:                                      # noqa: BLE001
        logger.error("[Q2] platform spend alarm check failed (non-fatal): %r", e)
        return {"alarmed": False, "error": str(e)}
