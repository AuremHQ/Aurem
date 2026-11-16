/**
 * lib/billingCopy.js — Master Prompt v2 PART 5.1.
 *
 * The ONE place every "N free tasks" / "N tasks/month" piece of copy
 * reads from, so a cutover to token wording is a single flip here
 * instead of hand-editing a dozen pages.
 *
 * TOKEN_WORDING_ACTIVE mirrors the backend's TOKEN_BILLING_MODE
 * (services.wallet.billing_mode()) via a build-time env var,
 * VITE_TOKEN_BILLING_MODE — false (legacy wording) until that env var
 * is set to something other than "off"/unset at a real cutover
 * build. Any unrecognised value fails SAFE to legacy wording, same
 * fail-safe convention as billing_mode() itself.
 *
 * Per PART 5.1's explicit rule, NO line here hardcodes an "about N
 * fixes" estimate — that number only ever comes from the live
 * rolling estimate (GET /payments/fix-estimate, already wired into
 * PaywallModal.jsx) and is hidden when no estimate exists. The
 * constants below are token AMOUNTS (a pure $-to-tokens fact, not an
 * estimate) and task-count-free marketing copy only.
 */
const _VALID_MODES = new Set(["off", "shadow", "on"]);

function _mode() {
  const raw = (
    (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_TOKEN_BILLING_MODE) || "off"
  ).trim().toLowerCase();
  return _VALID_MODES.has(raw) ? raw : "off";
}

export const TOKEN_WORDING_ACTIVE = _mode() !== "off";

// ITEM 5(b) (independent review) — exposes the raw build-time mode
// string (not just the off/not-off boolean above) so
// lib/billingModeCheck.js can compare it against the backend's live
// runtime value and detect drift.
export function buildTimeBillingMode() {
  return _mode();
}

// $9/$19/$49 at 100 tokens/USD (config/token_pricing.py::USD_TO_TOKENS) —
// matches the amounts the Stripe webhook already grants
// (routers/payments.py's plan_token_grants).
export const PLAN_TOKENS = { starter: 900, pro: 1900, team: 4900 };

export function heroFreeOfferCta() {
  return TOKEN_WORDING_ACTIVE ? "Start free — get your score" : "Start free — 10 tasks";
}

export function heroFreeOfferSubline() {
  return TOKEN_WORDING_ACTIVE
    ? "One free full scan — no credit card required · 30-second signup"
    : "10 free tasks — no credit card required · 30-second signup";
}

export function signupFreeOfferTitle() {
  return TOKEN_WORDING_ACTIVE ? "Sign up · Claim your free scan · ORA by Aurem" : "Sign up · Claim 10 free tasks · ORA by Aurem";
}

export function signupFreeOfferDescription() {
  return TOKEN_WORDING_ACTIVE
    ? "Create your AUREM account in 30 seconds. One free full repo scan on signup — no credit card required. Bring your own Anthropic, DeepSeek or Gemini key."
    : "Create your AUREM account in 30 seconds. 10 free tasks on signup — no credit card required. Bring your own Anthropic, DeepSeek or Gemini key.";
}

export function loginFreeOfferCta() {
  return TOKEN_WORDING_ACTIVE ? "Claim your free scan →" : "Claim 10 free tasks →";
}

export function defaultPageMetaDescription() {
  return TOKEN_WORDING_ACTIVE
    ? "AUREM is an autonomous AI engineer that plans, writes, tests and ships features to your GitHub repo. One free full scan, no card required."
    : "AUREM is an autonomous AI engineer that plans, writes, tests and ships features to your GitHub repo. 10 free tasks, no card required.";
}

export function freeOfferShortLine() {
  return TOKEN_WORDING_ACTIVE ? "Free scan. No credit card." : "10 free tasks. No credit card.";
}

/** PricingCards.jsx / UsageLimitModal.jsx per-tier capacity line.
 * Never hardcodes a fixes estimate — tokens only, a pure $ fact. */
export function tierCapacityLine(tierId) {
  if (!TOKEN_WORDING_ACTIVE) return null; // caller falls back to its own legacy text
  if (tierId === "free") return "1 free full scan";
  const tokens = PLAN_TOKENS[tierId];
  return tokens ? `${tokens.toLocaleString()} tokens / month` : null;
}

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

export function pricingDetailLine() {
  return TOKEN_WORDING_ACTIVE
    ? "One flat monthly price. No per-action billing — pay for your plan's token allotment, not per-request."
    : "One flat monthly price. No token meters — pay for your plan's task allotment, not per-request.";
}

export function landingPricingSectionSub() {
  return TOKEN_WORDING_ACTIVE
    ? "One flat monthly price for your plan's token allotment. No per-action billing — your balance just tracks total usage. Annual plans save 20%."
    : "One flat monthly price for your plan's task allotment. No token meters, no per-request billing. Annual plans save 20%.";
}

// Landing.jsx hero-stats bar's 2nd tile ("0" / "token billing") — the
// same false-in-token-mode claim as heroTokenMetersClaim, in a
// different spot. No confident replacement stat was clear enough to
// invent (see MISSING DECISIONS) — null means "omit this tile."
export function heroStatTokenBilling() {
  return TOKEN_WORDING_ACTIVE ? null : { num: "0", label: "token billing" };
}
