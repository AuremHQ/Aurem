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
