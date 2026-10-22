"""
config/token_pricing.py — Master Prompt Step B3: single source of truth
for USD<->token conversion and the per-action token price table.

1 USD = 100 tokens (USD_TO_TOKENS). A $9 plan therefore grants 900
tokens; this module is the ONE place that fact is computed, so backend
enforcement (services/wallet.py) and any UI display read the identical
numbers — they can never drift apart.

PRICE_TABLE values are PLACEHOLDERS. Every value is marked "estimate;
measure with 50 real runs" because no real per-action LLM-cost
measurement has been done yet for the token-billing model (see Step A7
audit — the one historical cost figure found, $0.0163/scan, predates
the current zero-LLM-cost scan pipeline and is NOT reusable here).
Replace each estimate with a real median once Step B9's cost-cap
telemetry has 50 real runs of that action to measure from.

HARD RULE (do not violate): never raise a token price for an action
that EXISTING users already have tokens priced against. If a price
must go up, ship it as a NEW action key (e.g. "fix_v2") and leave the
old key's price untouched for any in-flight hold/settle still
referencing it — a price change must never retroactively make a
user's already-granted tokens buy less than they did when granted.
"""
from __future__ import annotations

# ── The ONE conversion rate. Everything else derives from it. ──────────
USD_TO_TOKENS: int = 100


def usd_to_tokens(usd: float) -> int:
    return round(usd * USD_TO_TOKENS)


def tokens_to_usd(tokens: int) -> float:
    return round(tokens / USD_TO_TOKENS, 4)


# ── Per-action token price table (Step B3). PLACEHOLDERS — see module
# docstring. "fix" is the only action gated today (Step C2); the rest
# are defined now so Step D/E's UI and any future gate have a single
# place to read from, without needing a second table added later.
#
# *** fix=12 / loop_run=40 / chat_turn=2 ARE NOT FINAL NUMBERS. ***
# They are guesses standing in until Step B9's real cost telemetry has
# 50 runs of each action to measure a real median from. Concretely,
# "$9 = 900 tokens" (USD_TO_TOKENS above) combined with these
# placeholders implies the $9/mo plan currently "buys" 900/12 = 75
# fixes, or 900/40 = 22 loop runs, or 900/2 = 450 chat turns, per
# month — at TODAY'S guessed prices. If the real median fix cost turns
# out to be, say, 20 tokens instead of 12, that arithmetic changes
# (75 fixes becomes 45) even though nothing about the $9 price or the
# USD_TO_TOKENS rate moved. Don't quote "N fixes per plan" anywhere
# (support copy, pricing page, docs) as if it were a committed number
# until these are real.
PRICE_TABLE: dict[str, int] = {
    # estimate; measure with 50 real runs — a single CTO-task fix
    # (codegen + hallucination/syntax/lint gates + Vanguard verify +
    # one commit). Outcome price shown to users: ~$0.12 (12 tokens).
    "fix":            12,
    # estimate; measure with 50 real runs — a Loop-Mode run (plan ->
    # execute -> verify -> ship), historically more expensive than a
    # single fix per services/llm_cost_breaker.py's per-loop cap notes.
    "loop_run":       40,
    # estimate; measure with 50 real runs — one chat turn with real
    # tool use (not a trivial reply).
    "chat_turn":       2,
}


def tokens_for(action: str) -> int:
    """Raises KeyError for an unknown action — callers must register a
    price here before gating a new action; there is no silent default
    (a silent 0-token default would let an ungated action through for
    free in "on" mode, and a silent large default would wrongly block
    a cheap action)."""
    return PRICE_TABLE[action]


def outcome_price_usd(action: str) -> float:
    """What the UI shows BEFORE a user has a paid plan — an outcome
    price in dollars, never a token count (Step E1: "no token counts
    here, show outcome prices")."""
    return tokens_to_usd(tokens_for(action))
