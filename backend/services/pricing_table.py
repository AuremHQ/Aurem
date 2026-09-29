"""
services/pricing_table.py — 2026-09-27, MASTER TASK Part A #5

ONE canonical LLM price table for the whole app, priced PER MILLION
tokens (matching every provider's own official pricing page — NOT
"per 1k", which is where the previous bugs came from: the old
`cost_per_1k = {"deepseek": 0.30, ...}` table in
routers/admin_analytics.py was actually a per-MILLION rate mislabeled
as per-1k, silently inflating every DeepSeek-attributed cost by
~1000x (a 3,575-token DeepSeek call should cost about a tenth of a
cent, not $1.07).

Real provider-reported cost (OpenRouter's `usage.cost` field on the
raw response) is ALWAYS preferred over this table when the provider
gives it to us directly — this table is the fallback for the two
non-OpenRouter paths in this codebase (Groq's own API, DeepSeek's own
direct API) where no such field exists, and it's also the ONE place
future models get added instead of another ad-hoc hardcoded number.

Prices verified 2026-09-27 against OpenRouter's live /api/v1/models
endpoint (for the three OpenRouter-routed models) and each provider's
own pricing page (for the two direct-API paths). Update this file,
and only this file, when prices change.
"""
from __future__ import annotations

# { model_id: (input_$_per_million, output_$_per_million, cache_read_$_per_million or None) }
PRICE_TABLE_PER_MILLION: dict[str, tuple[float, float, float | None]] = {
    # OpenRouter-routed (verified live via GET https://openrouter.ai/api/v1/models, 2026-09-27)
    "deepseek/deepseek-chat":       (0.2574, 1.0287, None),
    "z-ai/glm-5.2":                 (0.6496, 2.0416, 0.12064),
    "anthropic/claude-sonnet-4.5":  (3.00,   15.00,  0.30),
    "openai/gpt-oss-20b":           (0.018,  0.09,   None),
    # Non-OpenRouter direct APIs — no `usage.cost` field on their
    # responses, so this table is the ONLY source of cost for these.
    "deepseek-v4-flash":            (0.14,   0.28,   0.0028),  # DeepSeek official API, cache-miss rate
    "llama-3.3-70b-versatile":      (0.59,   0.79,   None),    # Groq official pricing
}

_UNKNOWN_MODEL_FALLBACK = (1.00, 3.00, None)  # deliberately conservative (over-, not under-, estimate)


def compute_cost_usd(model: str, input_tokens: int, output_tokens: int,
                      cached_tokens: int = 0) -> float:
    """Fallback cost calculator for calls whose provider response has
    no real `usage.cost` field (Groq, DeepSeek-direct). Always prefer
    the provider-reported cost when it exists — this is a best-effort
    second source, not the primary one."""
    in_price, out_price, cache_price = PRICE_TABLE_PER_MILLION.get(
        model, _UNKNOWN_MODEL_FALLBACK)
    billable_input = max(0, input_tokens - cached_tokens)
    cost = (billable_input / 1_000_000) * in_price
    cost += (output_tokens / 1_000_000) * out_price
    if cached_tokens and cache_price is not None:
        cost += (cached_tokens / 1_000_000) * cache_price
    return round(cost, 8)
