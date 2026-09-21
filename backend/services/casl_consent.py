"""
services/casl_consent.py — CASL implied-consent expiry gate (2026-09-22)

Founder's ask (Revenue Recovery blueprint): before ANY segment-based
re-engagement/win-back list is built off real users, every recipient
must clear a CASL (Canada's Anti-Spam Legislation) implied-consent
expiry check. This is HARD-CODED date arithmetic — deliberately NOT an
AI/LLM judgment call at send time, per explicit founder instruction.

CASL implied consent (existing business relationship) lasts:
  · 24 months (730 days) from the most recent PURCHASE/paid transaction
  · 6 months  (182 days) from an INQUIRY (signup with no purchase ever)
Whichever basis applies, once expired the contact must be excluded from
any commercial-electronic-message send — not silently skipped, FLAGGED,
with a visible excluded count so a human can see the gate actually ran.

This module is read-only: it classifies a list of user dicts, it does
not send anything and does not write to Stripe or any production write
path.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

CASL_INQUIRY_WINDOW_DAYS  = 182   # ~6 months, no-purchase "inquiry" basis
CASL_PURCHASE_WINDOW_DAYS = 730   # 24 months, existing-business-relationship basis


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dt(raw) -> Optional[datetime]:
    """Coerce epoch float/int, aware/naive datetime, or ISO string -> aware dt."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        secs = float(raw) / (1000.0 if raw > 10 ** 12 else 1.0)
        try:
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(raw, str):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


async def _last_purchase_at(db, user_id: str) -> Optional[datetime]:
    """Most recent successful payment OR active/canceled subscription
    start for this user, if any. Read-only lookups against `payments`
    and `subscriptions` (services/payment_reconciliation.py's local
    mirror of Stripe state) — no Stripe API calls made here."""
    latest: Optional[datetime] = None
    try:
        p = await db.payments.find_one(
            {"user_id": user_id, "status": "paid"},
            {"_id": 0, "created_at": 1},
            sort=[("created_at", -1)],
        )
        latest = _as_dt((p or {}).get("created_at"))
    except Exception:
        pass
    try:
        sub = await db.subscriptions.find_one(
            {"user_id": user_id, "status": {"$in": ["active", "canceled", "past_due"]}},
            {"_id": 0, "created_at": 1},
            sort=[("created_at", -1)],
        )
        sub_dt = _as_dt((sub or {}).get("created_at"))
        if sub_dt and (latest is None or sub_dt > latest):
            latest = sub_dt
    except Exception:
        pass
    return latest


def casl_check(*, inquiry_at: Optional[datetime], purchase_at: Optional[datetime],
                now: Optional[datetime] = None) -> dict:
    """Pure function, no DB access — the actual hard-coded gate.
    Returns {eligible, basis, reference_date, days_since, reason}."""
    now = now or _now()
    if purchase_at is not None:
        days_since = (now - purchase_at).days
        eligible = days_since <= CASL_PURCHASE_WINDOW_DAYS
        return {
            "eligible":        eligible,
            "basis":           "purchase",
            "reference_date":  purchase_at.isoformat(),
            "days_since":      days_since,
            "reason":          None if eligible else "casl_expired_purchase",
        }
    if inquiry_at is not None:
        days_since = (now - inquiry_at).days
        eligible = days_since <= CASL_INQUIRY_WINDOW_DAYS
        return {
            "eligible":        eligible,
            "basis":           "inquiry",
            "reference_date":  inquiry_at.isoformat(),
            "days_since":      days_since,
            "reason":          None if eligible else "casl_expired_inquiry",
        }
    # No signup date and no purchase — nothing to base consent on at all.
    return {
        "eligible": False, "basis": "none", "reference_date": None,
        "days_since": None, "reason": "casl_no_consent_basis",
    }


async def casl_screen(db, users: list[dict]) -> dict:
    """Batch gate for a candidate re-engagement/win-back segment.

    `users` items need `user_id`, `email`, and `created_at` (signup/
    inquiry date fallback). Returns eligible + excluded lists — the
    excluded list is never silently dropped, it's returned with the
    reason and reference date so a human can audit it.
    """
    now = _now()
    eligible: list[dict] = []
    excluded: list[dict] = []
    for u in users:
        uid = u.get("user_id")
        inquiry_at = _as_dt(u.get("created_at"))
        purchase_at = await _last_purchase_at(db, uid) if (db is not None and uid) else None
        result = casl_check(inquiry_at=inquiry_at, purchase_at=purchase_at, now=now)
        row = {
            "user_id": uid, "email": u.get("email"),
            "basis": result["basis"], "reference_date": result["reference_date"],
            "days_since": result["days_since"],
        }
        if result["eligible"]:
            eligible.append(row)
        else:
            row["reason"] = result["reason"]
            excluded.append(row)
    return {
        "eligible":          eligible,
        "excluded":          excluded,
        "eligible_count":    len(eligible),
        "excluded_count":    len(excluded),
        "total_screened":    len(users),
        "checked_at":        now.isoformat(),
        "inquiry_window_days":  CASL_INQUIRY_WINDOW_DAYS,
        "purchase_window_days": CASL_PURCHASE_WINDOW_DAYS,
    }


# ── Weekly digest (2026-09-22) ────────────────────────────────────────
# Founder's ask: "CASL-screen ko weekly digest banao" — turn the
# read-only casl-screen endpoint into a standing weekly email so the
# excluded count is visible WITHOUT anyone having to remember to hit
# the admin endpoint before building a win-back list. Follows the
# exact same weekday/hour scheduler + Resend-to-ADMIN_EMAIL pattern as
# services/journey_watch.py::schedule_funnel_digest_cron (funnel
# digest) and services/daily_digest.py — same _send_via_resend helper,
# same ADMIN_EMAIL env var, no new delivery mechanism invented.
import logging as _logging

logger = _logging.getLogger("aurem.casl_consent")

DIGEST_COLLECTION = "casl_digest_sends"


def _digest_enabled() -> bool:
    v = (os.environ.get("ENABLE_CASL_DIGEST_CRON") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


async def build_casl_digest(db) -> dict:
    """Screen the CURRENT stuck-user segment (same segment any future
    win-back list would be built from) and summarize eligible/excluded
    per funnel stage plus a grand total."""
    from services.funnel_nudge_cron import STAGES, classify_users
    buckets = await classify_users(db)
    per_stage: dict[str, dict] = {}
    total_eligible = 0
    total_excluded = 0
    total_screened = 0
    reason_counts: dict[str, int] = {}
    for stage in STAGES:
        result = await casl_screen(db, buckets.get(stage, []))
        per_stage[stage] = {
            "eligible_count": result["eligible_count"],
            "excluded_count": result["excluded_count"],
            "total_screened": result["total_screened"],
        }
        total_eligible += result["eligible_count"]
        total_excluded += result["excluded_count"]
        total_screened += result["total_screened"]
        for row in result["excluded"]:
            reason_counts[row["reason"]] = reason_counts.get(row["reason"], 0) + 1
    return {
        "generated_at":   _now().isoformat(),
        "per_stage":      per_stage,
        "total_eligible": total_eligible,
        "total_excluded": total_excluded,
        "total_screened": total_screened,
        "excluded_by_reason": reason_counts,
    }


def _render_casl_digest_text(digest: dict) -> str:
    lines = [
        f"AUREM — Weekly CASL Consent Digest ({digest['generated_at'][:10]})",
        "",
        f"Total stuck-user segment screened: {digest['total_screened']}",
        f"  CASL-eligible (safe to include in a win-back send): {digest['total_eligible']}",
        f"  CASL-excluded (consent window expired — DO NOT email): {digest['total_excluded']}",
        "",
        "Excluded by reason:",
    ]
    if digest["excluded_by_reason"]:
        for reason, n in digest["excluded_by_reason"].items():
            lines.append(f"  - {reason}: {n}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("Per funnel stage:")
    for stage, s in digest["per_stage"].items():
        lines.append(
            f"  {stage}: {s['eligible_count']} eligible / "
            f"{s['excluded_count']} excluded / {s['total_screened']} total"
        )
    lines.append("")
    lines.append(
        "This is a read-only screen — nothing was sent or written. "
        "Run GET /admin/onboarding-funnel/casl-screen for the full "
        "per-user list before building any send."
    )
    return "\n".join(lines)


async def _already_sent_this_week(db, week_key: str) -> bool:
    try:
        row = await db[DIGEST_COLLECTION].find_one({"week": week_key})
        return row is not None
    except Exception:
        return False


async def run_casl_digest_once(db) -> dict:
    """One digest tick — idempotent per ISO week (dedup collection),
    logs always, emails ADMIN_EMAIL if Resend is configured."""
    now = _now()
    week_key = now.strftime("%G-W%V")
    if await _already_sent_this_week(db, week_key):
        return {"sent": False, "reason": "already_sent_this_week", "week": week_key}
    digest = await build_casl_digest(db)
    body = _render_casl_digest_text(digest)
    logger.info("📋 CASL WEEKLY DIGEST\n%s", body)
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip()
    delivered = False
    if admin_email:
        from services.daily_digest import _send_via_resend
        delivered = await _send_via_resend(
            admin_email, "AUREM — Weekly CASL Consent Digest", body,
        )
        if not delivered:
            logger.info("RESEND_API_KEY not set — CASL digest only logged.")
    try:
        await db[DIGEST_COLLECTION].insert_one({
            "week": week_key, "sent_at": now, "delivered": delivered,
            **digest,
        })
    except Exception:
        pass
    return {"sent": True, "delivered": delivered, "week": week_key, **digest}


async def schedule_casl_weekly_digest() -> None:
    """Weekly loop — same weekday/hour scheduling shape as
    schedule_funnel_digest_cron, offset by 1h so the two digests don't
    fire in the same tick."""
    import asyncio
    from datetime import timedelta as _td
    from cto_services.db import get_db

    if not _digest_enabled():
        logger.info("casl_digest_cron disabled (ENABLE_CASL_DIGEST_CRON=0)")
        return
    target_weekday = int(os.environ.get("CASL_DIGEST_WEEKDAY_UTC", "0"))  # Monday
    target_hour = int(os.environ.get("CASL_DIGEST_HOUR_UTC", "9"))  # 1h after funnel digest
    while True:
        now = _now()
        days_ahead = (target_weekday - now.weekday()) % 7
        next_run = (now + _td(days=days_ahead)).replace(
            hour=target_hour, minute=0, second=0, microsecond=0,
        )
        if next_run <= now:
            next_run += _td(days=7)
        wait_s = (next_run - now).total_seconds()
        try:
            await asyncio.sleep(wait_s)
            db = get_db()
            if db is not None:
                await run_casl_digest_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("casl digest scheduler crash")
            await asyncio.sleep(3600)
