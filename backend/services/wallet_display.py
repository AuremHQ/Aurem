"""
services/wallet_display.py — Master Prompt v2 PART 4.3, the user-
facing token METER's one data source.

Strict display rules (verbatim from the spec, enforced HERE so no
caller can accidentally leak more):
  - visible only when the user has a paid plan (tier != "free") AND
    is not unlimited AND TOKEN_BILLING_MODE is not "off" — while the
    mode stays "off" in this delivery, the meter stays invisible for
    every real user, same as every other billing_mode()-gated path
    in this codebase.
  - the ONLY numbers returned are the remaining balance, the month's
    starting balance ("base", for the progress bar), the refill date,
    and a threshold tag — never a per-action/per-tool-call cost, never
    a dollar amount, never another user's data (this function takes a
    single user_id and only ever reads that one wallet).

Thresholds are computed from % of `base` remaining: >20% ok, 20%-5%
(inclusive of the 20% boundary) yellow, <5% (inclusive of 5%) down to
>0 red, 0 stop. `base` of None/0 (wallet never granted yet) means
there's nothing to compute a percentage against — pct_remaining is
None rather than a divide-by-zero or a misleading 0%.
"""
from __future__ import annotations

from services.wallet import billing_mode, get_balance


async def get_user_meter(db, user_id: str) -> dict:
    user = await db.dev_users.find_one(
        {"user_id": user_id}, {"tier": 1, "is_unlimited": 1},
    ) or {}
    tier = user.get("tier", "free")
    is_unlimited = bool(user.get("is_unlimited")) or tier == "founder"

    if billing_mode() == "off" or tier == "free" or is_unlimited:
        return {
            "visible": False, "balance": None, "base": None,
            "pct_remaining": None, "refill_date": None, "threshold": None,
        }

    bal = await get_balance(db, user_id)
    balance = bal["spendable"]
    base = bal.get("month_baseline")

    pct_remaining = None
    if base:
        pct_remaining = round(100.0 * balance / base, 1)

    if balance <= 0:
        threshold = "stop"
    elif pct_remaining is not None and pct_remaining <= 5:
        threshold = "red"
    elif pct_remaining is not None and pct_remaining <= 20:
        threshold = "yellow"
    else:
        threshold = "ok"

    return {
        "visible": True,
        "balance": balance,
        "base": base,
        "pct_remaining": pct_remaining,
        "refill_date": bal.get("plan_refill_at"),
        "threshold": threshold,
    }
