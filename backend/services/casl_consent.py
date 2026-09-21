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
