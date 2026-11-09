/**
 * billingCopy.js — token-mode vs task-mode wording helpers.
 *
 * When TOKEN_WORDING_ACTIVE is true, all pricing/task displays
 * switch to token-based equivalents. Legacy mode keeps showing
 * task counts until VITE_TOKEN_BILLING_MODE is enabled.
 */
import { PLAN_TOKENS } from "./subscriptionTiers";

export const TOKEN_WORDING_ACTIVE = import.meta.env.VITE_TOKEN_BILLING_MODE === "true";

export function heroFreeOfferCta() {
  return TOKEN_WORDING_ACTIVE
    ? "10,000 free tokens"
    : "10 free tasks";
}

export function heroTokenMetersClaim() {
  return TOKEN_WORDING_ACTIVE
    ? "Token meters track usage across all modes"
    : "Task meters track usage across all modes";
// ITEM 6 (independent review) — "No IDE, no token meters." (Landing.jsx
// hero headline) becomes FALSE once token billing is live: the
// product explicitly has a token meter then (TokenMeter.jsx).
// 2026-10-27 — founder decision (replaces the earlier "render
// nothing" placeholder): token mode now shows an honest design-goal
// claim instead ("lowest token usage"), not a stat/percentage that
// would need re-derivation.
export function heroTokenMetersClaim() {
  return TOKEN_WORDING_ACTIVE ? "Engineered for the lowest token usage." : "No IDE, no token meters.";
}

// Landing.jsx's comparison-section sub-head + its "Flat pricing (no
// token meter)" compare-table row share the SAME false-in-token-mode
// claim as heroTokenMetersClaim above, but in a secondary (not hero)
// spot — reuses the already-established "no per-action billing"
// replacement convention (pricingHeroHeadline/pricingDetailLine
// above), not the hero's own "lowest token usage" line, so the same
// claim isn't repeated three times on one page.
export function comparisonSectionSub() {
  return TOKEN_WORDING_ACTIVE
    ? "No IDE. No per-action billing. No guessing. No vendor lock."
    : "No IDE. No token meters. No guessing. No vendor lock.";
}

export function comparisonFlatPricingRowLabel() {
  return TOKEN_WORDING_ACTIVE ? "Flat pricing (no per-action billing)" : "Flat pricing (no token meter)";
}

// Landing.jsx's "$9/Month. No Surprises." feature card + its "How much
// does ORA cost?" FAQ answer — both state task-count/token-metering
// claims that are only true in legacy mode.
export function pricingFeatureCardDesc() {
  return TOKEN_WORDING_ACTIVE
    ? "No IDE to install. No per-action billing. No per-seat pricing. One flat monthly price per plan — $9, $19 or $49 — for that plan's token allotment. Unused tokens carry forward."
    : "No IDE to install. No token meters to watch. No per-seat pricing. Flat pricing: 10/50/300/400 tasks per month across Free/Starter/Pro/Team.";
}

export function howMuchFaqAnswer() {
  return TOKEN_WORDING_ACTIVE
    ? "ORA starts at $9/month flat, and you get one free full repo scan with no credit card. The $9 plan grants 900 tokens/month (900 to the dollar) — no per-seat pricing, and unused tokens carry forward to the next month."
    : "ORA starts at $9/month flat, and there are 10 tasks free with no credit card. The $9 plan has no token metering and no per-seat pricing — the same monthly price whether ORA runs 5 tasks or 500 per month.";
}

// Pricing.jsx h1. Same false-claim problem as above, but this one has
// a clear, already-established TRUE replacement: Part 4's own rule
// (enforced by scripts/pricing_copy_lint.py's per-action-cost check)
// is "never show a per-action number" — so "no per-action billing" is
// true under the token system, not an invented claim.
export function pricingHeroHeadline() {
  return TOKEN_WORDING_ACTIVE ? "Flat pricing. No per-action billing." : "Flat pricing. No token meters.";
}

export function heroStatTokenBilling() {
  return TOKEN_WORDING_ACTIVE
    ? "Token-based billing"
    : "Flat pricing";
}

export function landingPricingSectionSub() {
  return TOKEN_WORDING_ACTIVE
    ? "Token-based pricing with no surprises"
    : "Flat pricing with no surprises";
}

export function tierCapacityLine(tierId) {
  if (!TOKEN_WORDING_ACTIVE) return null;
  const tokens = PLAN_TOKENS[tierId];
  if (!tokens) return null;
  return `${tokens.toLocaleString()} tokens per month`;
}
