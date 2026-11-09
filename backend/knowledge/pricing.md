---
status: draft
approved_by: null
approved_on: null
inject_on: [price, pricing, cost, plan, tier, subscription, upgrade, how much, token balance]
max_chars: 1300
---

# Pricing (source: backend/services/subscription_tiers.py)

| Tier | Price / month | Tasks / month | Maxx tasks / month |
|---|---|---|---|
| Free | $0 | 10 | 0 |
| Starter | $9 | 50 | 0 |
| Pro | $19 | 300 | 100 (overage billed separately past that) |
| Team | $49 | 400 | unlimited |

Token conversion rate (used only when token billing is active, currently
off): 1 USD = 100 tokens (`config/token_pricing.py::USD_TO_TOKENS`).

## Rules for ORA when answering pricing questions

- Never state a per-action, per-task, or per-tool-call token cost to a
  user. Only the remaining balance is ever shown.
- Never claim a refund policy, an uptime guarantee, or an accuracy
  guarantee. None of those are confirmed, approved product facts.
- Never say "unlimited" for anything except Team's Maxx-task cap, which
  is genuinely uncapped in `subscription_tiers.py`.
- Any price or limit not in the table above is UNKNOWN — say so, do not
  guess.
