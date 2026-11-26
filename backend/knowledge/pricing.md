---
status: draft
approved_by: null
approved_on: null
inject_on: [price, pricing, cost, plan, tier, subscription, upgrade, how much, token balance]
max_chars: 1300
---

# Pricing (source: backend/config/token_pricing.py, 1 USD = 100 tokens; backend/services/wallet.py::plan_token_grants)

| Tier | Price / month | Tokens / month |
|---|---|---|
| Free | $0 | No monthly grant — free scan only |
| Starter | $9 | 900 |
| Pro | $19 | 1,900 |
| Team | $49 | 4,900 |

ORA bills in tokens, not tasks. Tokens pay for fixes, Loop-Mode runs, and chat turns. Only your remaining balance is ever shown.

## Rules for ORA when answering pricing questions

- Never state a per-action, per-task, or per-tool-call token cost to a user. Only the remaining balance is ever shown.
- Never quote "N fixes per plan" — per-action token prices are placeholders, not measured yet.
- Never claim a refund policy, an uptime guarantee, or an accuracy guarantee. None of those are confirmed, approved product facts.
- Never say "unlimited tokens" — no plan has unlimited tokens. Team includes Maxx mode with no separate Maxx cap.
- Any price or limit not in the table above is UNKNOWN — say so, do not guess.
