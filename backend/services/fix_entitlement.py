"""
services/fix_entitlement.py — Master Prompt Step C1/C2.

`dev_users.fix_entitlement` (bool, default False/absent) is the ONE
server-side source of truth for "is this user allowed to run Fix".

It becomes True ONLY via `grant_fix_entitlement()`, called ONLY from
the signature-verified Stripe webhook handler
(routers/payments.py::stripe_webhook, on checkout.session.completed /
invoice.paid / customer.subscription.updated-to-active). NEVER from a
redirect, a `success_url` query param, or any value the frontend
supplies — `routers/payments.py::payment_status` (the endpoint the
frontend polls after the Stripe redirect) only ever READS this field
below, it never writes it. There is no bypass path: a forged
`?upgraded=1` on the success URL, or a fabricated `session_id` passed
to the status-poll endpoint, cannot set this field — only a real
Stripe webhook with a valid signature can.

Gated by the SAME TOKEN_BILLING_MODE as services/wallet.py (off/shadow/
on) via `assert_fix_entitled()`, the one entry point callers use.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException

from services.wallet import billing_mode

logger = logging.getLogger("aurem.fix_entitlement")


async def is_fix_entitled(db, user_id: str) -> bool:
    u = await db.dev_users.find_one(
        {"user_id": user_id},
        {"_id": 0, "fix_entitlement": 1, "fix_entitlement_frozen": 1},
    )
    u = u or {}
    # Master Prompt v2 Part 1.3 — a Stripe dispute freezes Fix WITHOUT
    # destroying the underlying `fix_entitlement` value, so a resolved
    # (won) dispute can restore exactly what the user had before —
    # unlike revoke_fix_entitlement, which is a destructive "this
    # plan is over" write.
    if u.get("fix_entitlement_frozen"):
        return False
    return bool(u.get("fix_entitlement"))


async def grant_fix_entitlement(db, user_id: str, *, source: str) -> None:
    """Called ONLY from the verified Stripe webhook handler. `source`
    is a short tag (e.g. "stripe_webhook:starter") recorded for audit —
    never set this from anywhere else."""
    res = await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {
            "fix_entitlement": True,
            "fix_entitlement_source": source,
            "fix_entitlement_at": datetime.now(UTC).isoformat(),
        }},
    )
    if res.matched_count == 0:
        # Should never happen for a real paying user (they signed up
        # already) — but fail LOUD rather than silently no-op'ing, so
        # a webhook race against a not-yet-visible dev_users row is
        # surfaced (the caller re-raises and Stripe retries) instead
        # of quietly leaving a paid user unentitled.
        logger.error(
            "[fix_entitlement] grant target user_id=%s matched ZERO "
            "dev_users rows (source=%s) — entitlement NOT recorded",
            user_id, source,
        )
        raise ValueError(f"no dev_users row for user_id={user_id!r} — cannot grant entitlement")
    logger.info("[fix_entitlement] granted user=%s source=%s", user_id, source)


async def revoke_fix_entitlement(db, user_id: str, *, reason: str) -> None:
    """Called when a subscription is cancelled/payment fails hard
    (webhook `customer.subscription.deleted`) — kept separate from
    grant for a clear, searchable audit trail of WHY it was revoked."""
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {
            "fix_entitlement": False,
            "fix_entitlement_revoked_reason": reason,
            "fix_entitlement_revoked_at": datetime.now(UTC).isoformat(),
        }},
    )
    logger.info("[fix_entitlement] revoked user=%s reason=%s", user_id, reason)


async def freeze_fix_entitlement(db, user_id: str, *, reason: str) -> None:
    """Master Prompt v2 Part 1.3 — a Stripe `charge.dispute.created`
    freezes Fix access until a human resolves the dispute. Does NOT
    touch the underlying `fix_entitlement` bool, so unfreezing
    restores exactly what the user had — a destructive
    revoke_fix_entitlement() would lose that. There is no automatic
    unfreeze webhook wired yet (only charge.dispute.created was asked
    for, not .closed) — see routers/payments.py's webhook docstring."""
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {
            "fix_entitlement_frozen": True,
            "fix_entitlement_frozen_reason": reason,
            "fix_entitlement_frozen_at": datetime.now(UTC).isoformat(),
        }},
    )
    logger.info("[fix_entitlement] FROZEN user=%s reason=%s", user_id, reason)


async def unfreeze_fix_entitlement(db, user_id: str, *, reason: str) -> None:
    """Admin-triggered only today (no automatic dispute-resolution
    webhook wired) — restores whatever `fix_entitlement` already was."""
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {
            "fix_entitlement_frozen": False,
            "fix_entitlement_unfrozen_reason": reason,
            "fix_entitlement_unfrozen_at": datetime.now(UTC).isoformat(),
        }},
    )
    logger.info("[fix_entitlement] unfrozen user=%s reason=%s", user_id, reason)


async def assert_fix_entitled(db, user_id: str) -> dict:
    """The ONE mode-aware entry point. off -> no-op (never blocks,
    matches today's un-gated behavior). shadow -> computes/logs what
    WOULD block, never raises. on -> raises a typed 402 when not
    entitled."""
    mode = billing_mode()
    if mode == "off":
        return {"mode": "off", "entitled": True}

    entitled = await is_fix_entitled(db, user_id)
    if mode == "shadow":
        if not entitled:
            logger.info(
                "[fix-entitlement-shadow] user=%s WOULD block — no "
                "fix_entitlement on file", user_id,
            )
        return {"mode": "shadow", "entitled": entitled}

    # mode == "on"
    if not entitled:
        raise HTTPException(402, detail={
            "code": "payment_required",
            "plans_url": "/settings#pricing",
        })
    return {"mode": "on", "entitled": True}
