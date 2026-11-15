# Token billing cutover checklist

Ordered steps for flipping `TOKEN_BILLING_MODE` from `off` to `shadow`,
and later `shadow` to `on`. Each step names who does it and how to
verify it before moving to the next. Nothing in this document has
been executed — this is the plan, not a log.

## 0. Pre-flight (read-only, repeatable, safe to run anytime)

| Step | Who | How to verify |
|---|---|---|
| Run the stale-entitlement pre-flight (`scripts/fix_entitlement_preflight.py` or `GET /admin-pricing/stale-fix-entitlement-preflight`) and review the CSV with the founder. See `docs/fix_entitlement_revoke_cutover_step.md`. | Engineer + founder | CSV count reviewed; founder has decided per-row what (if anything) to revoke before `on`. |
| Run the Part 6 plan-transition dry run (`GET /admin-pricing/plan-transition-dry-run`) and review the per-segment counts + total proposed liability. | Engineer + founder | Founder has reviewed `needs_decision` rows and the `MISSING DECISIONS` list from that report. |
| Run `maxx_overage_preflight_report` (services/billing_cron.py) to see what overage-invoicing has been silently deferred since shadow/on paused it. | Engineer + founder | Founder has decided whether to ever invoice the deferred amount (not built; see Item 4 report). |

## 1. Set both flags (same deploy)

| Step | Who | How to verify |
|---|---|---|
| Set backend `TOKEN_BILLING_MODE=shadow` (env var, next deploy). | Engineer | `GET /payments/billing-mode` (public, no auth) returns `{"mode": "shadow"}` after deploy. |
| Set frontend `VITE_TOKEN_BILLING_MODE=shadow` in the SAME deploy's build. | Engineer | Built bundle's `lib/billingCopy.js::TOKEN_WORDING_ACTIVE` is `true` — spot-check a page's copy. |

**Why both in the same deploy, not sequenced:** `TOKEN_BILLING_MODE` is
read at backend runtime (every request), but
`VITE_TOKEN_BILLING_MODE` is baked into the frontend bundle at BUILD
time. Flipping only the backend flag first makes `billing_mode()`
start returning `"shadow"` immediately while the ALREADY-DEPLOYED
frontend bundle still shows legacy task wording — exactly the drift
Item 5 is about. `lib/billingModeCheck.js` (wired into
`RailShell.jsx`, the authenticated shell) will log a console warning
and fire one `billing_mode_drift` analytics event if this ever
happens anyway, as a safety net, not as the intended path.

## 2. Rebuild + redeploy (SEO prerender)

| Step | Who | How to verify |
|---|---|---|
| Rebuild the frontend (`yarn build`) — required because of the SEO prerender step; a redeploy without a rebuild ships the OLD prerendered HTML even with the new env var set. | Engineer | `yarn build` log; check the built `index.html` / prerendered pages contain the new copy (see Item 6's grep sweep for exactly which phrases to check). |
| CDN cache purge for the prerendered pages. | Engineer | Fetch the live homepage from an incognito/uncached client; confirm new copy, not a stale cached version. |

## 3. Stripe dashboard names

| Step | Who | How to verify |
|---|---|---|
| Confirm Stripe product/price names and descriptions shown at checkout match the token-mode story (not "10 tasks/month" style legacy copy), if any Stripe-side copy exists outside this codebase. | Founder (Stripe dashboard access) | Manual check of the live Stripe Checkout page for each plan. |

## 4. Admin hint rows / legacy users

| Step | Who | How to verify |
|---|---|---|
| Decide what happens to legacy (pre-cutover) users' `tokens_granted`/task-based history — this is the Part 6 plan-transition proposal (per-tier `tokens_per_old_task` grant), still a DRY RUN only; no real grant has been built. | Founder | Review `needs_decision`/`excluded` segment counts from the dry-run report; sign off on the real-apply design before anyone builds it. |
| Flag any admin screen that still shows task-count language for a legacy user (sweep per Item 6). | Engineer | Item 6's grep table, ungated-hit list resolved to zero (or explicitly deferred with a MISSING DECISIONS entry). |

## 5. Rollback

| Step | Who | How to verify |
|---|---|---|
| Flip `TOKEN_BILLING_MODE` back to `off` (backend env var) AND `VITE_TOKEN_BILLING_MODE` back to `off`, then rebuild + redeploy + purge cache — same two-sided requirement as the forward cutover in step 1-2. | Engineer | `GET /payments/billing-mode` returns `{"mode": "off"}`; frontend copy reverts; no drift warning in console/analytics within a few minutes of real traffic. |
| `assert_fix_entitled`/`assert_wallet`/`bill_maxx_overages` all short-circuit to their pre-cutover no-op behavior at `"off"` automatically (no separate code change needed — this is the existing, tested contract, not new rollback logic). | Engineer | Existing test suites for these (fix_entitlement, wallet, billing_cron) already assert mode-off behavior; no new verification needed beyond re-running them. |

## Open items this checklist deliberately does NOT resolve

- The real (non-dry-run) Part 6 token grant for legacy users — not built, founder sign-off required first.
- The real fix_entitlement revoke tool for stale rows found by the Item 1 pre-flight — not built, needs an admin-confirm-token flow first.
- Whether to ever bill the Maxx overage amounts deferred during shadow/on (Item 4) — not decided.
- Item 6/7's MISSING DECISIONS (copy gaps, refund-policy-file mismatch) — tracked separately in those items' reports.
