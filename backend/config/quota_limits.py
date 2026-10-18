"""
config/quota_limits.py — Q1 (2026-02) single source of truth for the
free-tier lifetime budget.

PHASE 0 FINDING this replaces: the free-tier token number used to be
hardcoded independently in THREE places (services/usage.py
PLAN_LIMITS, routers/auth.py signup `starting_tokens`, and nowhere
consistently derived from a real dollar figure). Every number below
is defined ONCE here; every other file imports from this module.

Evidence behind the numbers (production admin panel, 2026-02):
  - A real free-tier first scan (16 categories, longcat-2.0 +
    deepseek-chat) cost $0.0163 total.
  - The OLD free limit (1.0k tokens) was ~50x too small for that same
    scan to even register as "not exhausted" — it was a display/cost
    problem, not a real infra-cost problem.

FREE_LIFETIME_BUDGET_USD is the one number that matters — it is what
actually gets compared against real spend (`real_llm_usage.cost_usd`,
see services/usage.py::get_usage). TOKENS_PER_USD_DISPLAY exists
ONLY to render a human-friendly "~250k tokens" figure on top of that
same dollar number for UI/display purposes — it is NOT a second,
independently-tracked counter. Changing FREE_LIFETIME_BUDGET_USD
automatically changes the displayed token figure too.
"""
from __future__ import annotations

# ── The ONE real cap. Everything else in this file is derived from it. ──
FREE_LIFETIME_BUDGET_USD: float = 0.25

# Blended display-only conversion rate (tokens per $1), picked so the
# derived "~250k tokens" figure roughly matches what a real free-tier
# user actually gets from the cheap models (longcat-2.0/deepseek-chat)
# this plan is priced around — NOT a literal, enforced token ledger.
TOKENS_PER_USD_DISPLAY: int = 1_000_000

# Human-facing number shown on Settings/Admin ("~250k tokens"). Purely
# cosmetic — the real gate is always FREE_LIFETIME_BUDGET_USD above.
FREE_LIFETIME_TOKEN_DISPLAY: int = round(
    FREE_LIFETIME_BUDGET_USD * TOKENS_PER_USD_DISPLAY
)

# ── First-scan policy (Q1 decision — see PHASE0 report) ─────────────
# EXEMPT (not a separate hard-ceiling budget). Justification:
#   1. A scan's category set is server-controlled (services.codebase_
#      health_core.SCANNERS, a fixed/finite dict) — NOT client-
#      expandable — so worst-case per-scan cost is already bounded by
#      the existing code, independent of this flag.
#   2. Real observed full-scan cost is $0.0163 — ~15x under even this
#      new $0.25 lifetime budget, let alone a separate ceiling.
#   3. The exemption is naturally single-use per account: it only
#      applies when this is the user's account-wide FIRST-EVER scan
#      (`codebase_health_scans` count == 0), so it can't be replayed
#      by connecting more projects on the same account. Repeated-
#      account abuse (many accounts, each getting one "free" scan) is
#      Q2's job (disposable-domain block, per-IP signup rate limit),
#      not this flag's.
# A separate hard-ceiling would have required instrumenting a mid-loop
# cost check inside every one of the 16 category scanners for a
# problem the evidence says isn't a runaway-cost risk — disproportionate
# engineering cost for this ticket. Revisit if real-world first-scan
# cost ever moves far from the $0.0163 baseline above.
FIRST_SCAN_EXEMPT: bool = True
