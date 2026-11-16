"""
services/wallet_shadow.py — Phase 7 devil's-advocate audit finding.

services/wallet.py::assert_wallet()'s "shadow" mode already computes
what WOULD be charged/blocked and logs it — a real, deliberate, well-
built feature, not something this file reinvents. Its one gap: the
output only ever reached logger.info/logger.debug, with no durable,
queryable record — so there was no way to systematically validate
shadow predictions against the REAL, currently-enforced task-count
gate (services/usage.py::assert_has_task_budget) before ever
considering a TOKEN_BILLING_MODE flip to "on".

log_shadow_decision() is called from assert_wallet()'s existing
shadow branch (additive — the existing log lines are untouched) and
writes one row per decision to `wallet_shadow_log`.

compare_shadow_vs_task_based_gate() is the actual validation tool:
for every shadow decision in a time window, it looks up that user's
CURRENT task-based-gate state (services.usage.get_usage) and reports
whether the shadow prediction's `would_block` agrees with whether the
user is actually at/over their task cap right now. This is
necessarily a point-in-time comparison (get_usage reflects "now", not
"the user's exact state at occurred_at") — good enough to spot a
systematically-wrong shadow calculation (e.g. consistently predicting
blocks for users nowhere near their cap), not a perfect historical
backtest. Founders/is_unlimited users are excluded — they're
unlimited on the real gate by design, so including them would
manufacture false divergence signal.

Entirely additive and read-only with respect to billing itself:
nothing here ever calls hold()/settle()/release(), and a failure to
write a shadow-log row must never raise into assert_wallet()'s
caller (a billing bookkeeping failure must never mask the real
result of the request it's describing — same invariant
finalize_hold() already documents).
"""
from __future__ import annotations

import logging
import time
import uuid

from services.usage import get_usage

logger = logging.getLogger("aurem.wallet_shadow")

_COLLECTION = "wallet_shadow_log"


async def log_shadow_decision(
    db, *, user_id: str, action: str, needed_tokens: int,
    would_block: bool, spendable_balance: int,
) -> None:
    """Best-effort — never raises. Called only from assert_wallet()'s
    shadow branch; off/on modes never call this."""
    try:
        await db[_COLLECTION].insert_one({
            "shadow_id": f"wsh_{uuid.uuid4().hex[:12]}",
            "user_id": user_id,
            "action": action,
            "needed_tokens": needed_tokens,
            "would_block": would_block,
            "spendable_balance": spendable_balance,
            "occurred_at": time.time(),
        })
    except Exception as e:                                   # noqa: BLE001
        logger.warning("log_shadow_decision failed (non-fatal): %r", e)


async def compare_shadow_vs_task_based_gate(
    db, *, since_ts: float, until_ts: float,
) -> dict:
    """Window-scoped divergence report between shadow-mode
    predictions and the real, currently-enforced task-count gate.

    Returns:
      total_shadow_decisions, agreements, disagreements,
      excluded_unlimited, disagreement_examples (capped at 20).
    """
    rows = await db[_COLLECTION].find({
        "occurred_at": {"$gte": since_ts, "$lte": until_ts},
    }).to_list(10_000)

    total = 0
    agreements = 0
    disagreements = 0
    excluded_unlimited = 0
    examples: list[dict] = []

    for row in rows:
        try:
            usage = await get_usage(row["user_id"])
        except Exception as e:                               # noqa: BLE001
            logger.warning(
                "compare_shadow_vs_task_based_gate: get_usage failed "
                "for user=%s: %r", row.get("user_id"), e,
            )
            continue

        if usage.get("is_unlimited"):
            excluded_unlimited += 1
            continue

        cap = usage.get("monthly_task_cap")
        actually_at_cap = (
            cap is not None and usage.get("tasks_this_month", 0) >= cap
        )
        shadow_predicted_block = bool(row.get("would_block"))

        total += 1
        if shadow_predicted_block == actually_at_cap:
            agreements += 1
        else:
            disagreements += 1
            if len(examples) < 20:
                examples.append({
                    "user_id": row["user_id"],
                    "shadow_would_block": shadow_predicted_block,
                    "actually_at_cap": actually_at_cap,
                    "tasks_this_month": usage.get("tasks_this_month"),
                    "monthly_task_cap": cap,
                })

    return {
        "total_shadow_decisions": total,
        "agreements": agreements,
        "disagreements": disagreements,
        "excluded_unlimited": excluded_unlimited,
        "disagreement_examples": examples,
    }
