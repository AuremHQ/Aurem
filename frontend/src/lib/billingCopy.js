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
