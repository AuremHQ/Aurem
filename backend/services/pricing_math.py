"""services/pricing_math.py — leaf pricing arithmetic (no service imports).

Pure pricing math + constants: micro-token conversions, the actual-cost
charge equations, the spike guard, the effective pricing-version lookup
and the plan/purchased expiry default. They used to live in
services/pricing_engine.py, which made services/wallet.py import
pricing_engine for the charge math while pricing_engine imported
wallet.reconcile_report — a 2-node circular-import SCC that capped the
Architecture health score (circ x15). Nothing here imports any other
service (only stdlib os/time and function-level config.token_pricing),
so the cycle is broken at the root; pricing_engine.py re-exports every
moved name, so all existing importers keep working unchanged.

Moved 2026-11-20 (health-score architecture fix) — verbatim moves,
zero logic changes.
"""
from __future__ import annotations

import os
import time

MICRO_TOKENS_PER_TOKEN: int = 1_000_000

# Used only when no `pricing_versions` row exists yet (fresh install /
# before an admin has set one via Part 3's margin editor) — an explicit,
# clearly-labelled placeholder, same convention as token_pricing.py's
# own PRICE_TABLE estimates.
DEFAULT_MARGIN: float = 0.50
DEFAULT_STRIPE_FEE_PCT: float = 0.029
DEFAULT_STRIPE_FEE_FIXED_USD: float = 0.30

# Master Prompt v2 Part 2 rollover — None (default) means plan/purchased
# batches never expire, matching today's "purchased never expires"
# behavior extended to "plan" too. Set to an integer to start expiring
# batches older than that many months (swept by the monthly grant job).
TOKEN_EXPIRY_MONTHS: int | None = (
    int(os.environ["TOKEN_EXPIRY_MONTHS"])
    if os.environ.get("TOKEN_EXPIRY_MONTHS", "").strip()
    else None
)

# 3x spike guard (Part 2) — how far actual cost may exceed the held
# estimate before wallet.settle() caps the charge and flags it via
# services.llm_cost_breaker instead of passing the full overage to the
# user.
SPIKE_GUARD_MULTIPLE: float = 3.0
def usd_to_micro_tokens(usd: float) -> int:
    from config.token_pricing import USD_TO_TOKENS
    return round(usd * USD_TO_TOKENS * MICRO_TOKENS_PER_TOKEN)


def micro_tokens_to_tokens(micro_tokens: int) -> int:
    """Round to the NEAREST whole token — can legitimately be 0 for a
    genuinely tiny cost. PRE-STEP (b) correction (Master Prompt v2
    continuation): an earlier version forced a minimum of 1 token for
    any positive cost, reasoning that a $0 charge for real spend was
    worse than a tiny overcharge — but that silently overcharges a
    cheap action by orders of magnitude relative to what it actually
    cost. There is no accumulation of the rounded-away remainder across
    calls; `wallet.settle()`'s own `actual_cost_usd` ledger field still
    carries the TRUE cost for margin reporting regardless of how many
    tokens were actually collected from the user."""
    return max(0, round(micro_tokens / MICRO_TOKENS_PER_TOKEN))


def tokens_for_actual_cost(actual_cost_usd: float, *, margin: float) -> int:
    """wallet_tokens_charged = actual_llm_cost_usd * 100 / (1 - margin),
    rounded to the NEAREST whole token (never floored to a forced
    minimum — see `micro_tokens_to_tokens`). `margin` must be in
    [0, 1) — a margin of 1.0 or more would divide by zero or go
    negative, which means the caller passed a fraction instead of a
    percent (or a config bug); fail loud rather than silently charging
    an absurd amount.

    STALE-COMMENT FIX, PRE-STEP (a) continuation (Master Prompt v2) —
    this whole-token-rounded value is NO LONGER what services.wallet.
    settle() actually deducts from a user's balance (that was the
    ROUNDING LEAK: every call under ~$0.004 rounded to 0 tokens
    charged). settle() now calls `micro_tokens_for_actual_cost` below
    for the real, exact, never-rounded deduction; this function
    survives only for the dashboard's whole-token-ish display fields
    (`computed_tokens_uncapped`, `plan_capacity`) where rounding for
    a report is fine — it must never be used to compute a charge."""
    if actual_cost_usd <= 0:
        return 0
    if not (0 <= margin < 1):
        raise ValueError(f"margin must be in [0, 1), got {margin!r}")
    micro = usd_to_micro_tokens(actual_cost_usd) / (1 - margin)
    return micro_tokens_to_tokens(round(micro))


# PRE-STEP (a) continuation (Master Prompt v2) — the WALLET's own
# deduction precision. Distinct from MICRO_TOKENS_PER_TOKEN (1_000_000)
# above, which is this module's internal computation scale used only
# to avoid float error before a FINAL rounding step — this constant is
# the actual unit services.wallet stores and deducts in. 1 token =
# 1000 micro-tokens, as explicitly specified.
WALLET_MICRO_PER_TOKEN: int = 1000

# Same sane ceiling as services.wallet._MAX_SANE_COST_USD, duplicated
# here so this function is self-contained/safe even if called directly
# without going through wallet.settle()'s own clamp.
_MAX_SANE_COST_USD_FOR_MICRO: float = 1_000_000.0


def micro_tokens_for_actual_cost(actual_cost_usd: float, *, margin: float) -> int:
    """The EXACT wallet deduction, in micro-tokens (1 token = 1000
    micro-tokens) — never rounded to a whole token. This is what
    services.wallet.settle() actually deducts from the user's balance
    when a real cost is known; `tokens_for_actual_cost` above (whole-
    token rounded) exists only for legacy/display callers that
    predate this fix.

    PRE-STEP (a) ROUNDING LEAK fix (Master Prompt v2 continuation) —
    the previous "round to nearest whole token, allow 0" design meant
    every call costing under ~$0.004 (at a 20% margin) deducted ZERO
    tokens: a real, recurring free ride for high-frequency cheap
    actions (chat turns, classify, extract, summarize) while the
    platform still paid the real provider cost for every one of them.
    Only `services.wallet.get_balance()`'s legacy whole-token DISPLAY
    fields round now; the deduction itself is exact at micro-token
    resolution, with the sub-1000 remainder carried forward exactly
    (never dropped) via `services.wallet._apply_micro_refund`.

    margin must be in [0, 1) — see `tokens_for_actual_cost`."""
    if actual_cost_usd <= 0:
        return 0
    if not (0 <= margin < 1):
        raise ValueError(f"margin must be in [0, 1), got {margin!r}")
    from config.token_pricing import USD_TO_TOKENS
    cost = min(float(actual_cost_usd), _MAX_SANE_COST_USD_FOR_MICRO)
    micro = cost * USD_TO_TOKENS * WALLET_MICRO_PER_TOKEN / (1 - margin)
    return max(0, round(micro))


async def get_effective_pricing_version(db) -> dict:
    """The most recent version with effective_from <= now. Falls back
    to the hardcoded DEFAULT_* constants (tagged `_id: "default"`) if
    no row has been set yet — this module must never raise just
    because an admin hasn't opened the margin editor (Part 3) yet."""
    now = time.time()
    row = await db.pricing_versions.find_one(
        {"effective_from": {"$lte": now}},
        sort=[("effective_from", -1), ("seq", -1)],
    )
    if row:
        return row
    return {
        "_id": "default", "margin": DEFAULT_MARGIN,
        "stripe_fee_pct": DEFAULT_STRIPE_FEE_PCT,
        "stripe_fee_fixed_usd": DEFAULT_STRIPE_FEE_FIXED_USD,
        "effective_from": 0, "actor": "default",
    }


