"""
services/billing_flags.py — Master Prompt v2 Part 1.3.

A thin, append-only log of refund/dispute/cost events for human
review in the admin dashboard (routers/admin_pricing.py):

This is NOT a feature-flag / feature-toggle system, and it must not
be merged with services/feature_flags.py. The two modules share only
the word "flag": feature_flags answers "is this feature on for this
user?" (Mongo-backed toggles, 60s cache, rollout bucketing), while
this module only records that a billing event happened — it never
gates a feature. Fix #21 (2026-11-18): both systems are live and
serve different purposes, so neither was removed; this note exists
so the next audit does not try to merge them again.):

  - "refund_shortfall"   — a top-up refund asked for more tokens back
                            than the user's wallet still had (some/all
                            of that batch was already spent). No
                            clawback beyond the current balance ever
                            happens; this just records the gap.
  - "partial_sub_refund" — a partial refund of a subscription invoice
                            charge. Log-only per the founder-approved
                            default — no token/entitlement change.
  - "dispute_freeze"     — a charge.dispute.created froze fix_entitlement
                            until a human resolves it (no automatic
                            unfreeze path is built yet — see the
                            module docstring in routers/payments.py's
                            webhook handler for why).
- "dispute_won"        — a charge dispute closed in the merchant's
                         favour (charge.dispute.closed:won).
- "dispute_lost"       — a charge dispute closed in the customer's
                         favour; the entitlement is revoked and any
                         clawback shortfall is recorded
                         (charge.dispute.closed:lost).
- "cost_spike"         — an LLM cost spike tripped the cost breaker
                         (services/llm_cost_breaker.py).

Every row's `_id` is the idempotency_key (the Stripe event id) so a
redelivered webhook never double-logs. Writers are the Stripe
webhook handler in routers/payments.py and the LLM cost breaker;
every other part of the app is read-only via list_flags / resolve_flag.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger("aurem.billing_flags")


async def flag(db, *, flag_type: str, user_id: str, idempotency_key: str,
               detail: Optional[dict] = None) -> None:
    """Record a billing flag for admin review. Idempotent on
    `idempotency_key`; never raises — a flag-logging failure must
    never break the webhook it's describing."""
    try:
        await db.billing_flags.insert_one({
            "_id":        idempotency_key,
            "flag_type":  flag_type,
            "user_id":    user_id,
            "detail":     detail or {},
            "resolved":   False,
            "created_at": time.time(),
        })
    except DuplicateKeyError:
        return  # already logged for this event — no-op
    except Exception as e:                                    # noqa: BLE001
        logger.warning(
            "[billing_flags] flag write failed type=%s user=%s: %r",
            flag_type, user_id, e,
        )


# ── PART 3 — admin review list ──────────────────────────────────────────

async def list_flags(db, *, resolved: Optional[bool] = None, limit: int = 200) -> list[dict]:
    """Admin review list. `resolved=None` (default) returns both; pass
    True/False to filter to one state."""
    match: dict = {}
    if resolved is not None:
        match["resolved"] = resolved
    return await db.billing_flags.find(match).sort("created_at", -1).to_list(limit)


async def resolve_flag(db, flag_id: str, *, actor: str, note: str = "") -> dict:
    """Admin-only state change — marks a flag resolved. `note` is
    stored RAW (never HTML-escaped here — see services.pricing_engine.
    set_fixed_cost's docstring for why; the frontend's plain JSX text
    rendering is what prevents stored XSS, not a write-time mangling
    of the admin's own words)."""
    if not isinstance(flag_id, str):
        raise TypeError(f"flag_id must be a str, got {type(flag_id).__name__}: {flag_id!r}")
    doc = await db.billing_flags.find_one_and_update(
        {"_id": flag_id},
        {"$set": {
            "resolved": True, "resolved_by": actor,
            "resolved_at": time.time(), "resolution_note": note,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise ValueError(f"billing flag {flag_id!r} not found")
    return doc
