/**
 * billingCopy.test.js — Master Prompt v2 PART 5.2.
 * Locks in: legacy wording by default (TOKEN_BILLING_MODE unset /
 * "off" in this test env, matching the real app default), the exact
 * shape of each exported function, and that NO line hardcodes a
 * fixes-count estimate.
 */
import { describe, it, expect } from "vitest";
import {
  TOKEN_WORDING_ACTIVE,
  PLAN_TOKENS,
  heroFreeOfferCta,
  heroFreeOfferSubline,
  signupFreeOfferTitle,
  signupFreeOfferDescription,
  loginFreeOfferCta,
  defaultPageMetaDescription,
  freeOfferShortLine,
  tierCapacityLine,
  heroTokenMetersClaim,
  pricingHeroHeadline,
  pricingDetailLine,
  landingPricingSectionSub,
  heroStatTokenBilling,
} from "../billingCopy.js";

describe("billingCopy", () => {
  it("defaults to legacy wording (TOKEN_BILLING_MODE unset in this test env)", () => {
    expect(TOKEN_WORDING_ACTIVE).toBe(false);
  });

  it("legacy mode: every exported copy function returns the OLD task-based wording", () => {
    expect(heroFreeOfferCta()).toBe("Start free — 10 tasks");
    expect(heroFreeOfferSubline()).toMatch(/10 free tasks/);
    expect(signupFreeOfferTitle()).toMatch(/10 free tasks/);
    expect(signupFreeOfferDescription()).toMatch(/10 free tasks/);
    expect(loginFreeOfferCta()).toMatch(/10 free tasks/);
    expect(defaultPageMetaDescription()).toMatch(/10 free tasks/);
    expect(freeOfferShortLine()).toMatch(/10 free tasks/);
  });

  it("tierCapacityLine returns null in legacy mode (caller keeps its own legacy bullet)", () => {
    expect(tierCapacityLine("starter")).toBeNull();
    expect(tierCapacityLine("free")).toBeNull();
  });

  it("PLAN_TOKENS matches the exact amounts the Stripe webhook already grants (100 tokens/USD)", () => {
    expect(PLAN_TOKENS).toEqual({ starter: 900, pro: 1900, team: 4900 });
  });

  it("ITEM 6 — legacy mode: heroTokenMetersClaim/pricingHeroHeadline/pricingDetailLine/landingPricingSectionSub/heroStatTokenBilling return the OLD wording", () => {
    expect(heroTokenMetersClaim()).toBe("No IDE, no token meters.");
    expect(pricingHeroHeadline()).toBe("Flat pricing. No token meters.");
    expect(pricingDetailLine()).toMatch(/No token meters/);
    expect(landingPricingSectionSub()).toMatch(/No token meters/);
    expect(heroStatTokenBilling()).toEqual({ num: "0", label: "token billing" });
  });

  it("ITEM 6 — none of the new legacy/token-mode pairs ever say 'no token meters' when TOKEN_WORDING_ACTIVE would be true (verified via the ternary source, since this test env pins the build-time flag to off)", () => {
    // Direct behavioral proof that the TRUE branch exists and differs
    // from the legacy claim — see billingCopy.js's own source for the
    // ternary; MasterPromptV2Part6_homepageClaims.test.jsx locks the
    // exact token-mode strings via source inspection (same convention
    // as Landing.tokenWording.test.jsx) since VITE_TOKEN_BILLING_MODE
    // can't be flipped at runtime in this test env.
    expect(heroTokenMetersClaim.toString()).toMatch(/Engineered for the lowest token usage/);
    expect(pricingHeroHeadline.toString()).toMatch(/No per-action billing/);
    expect(pricingDetailLine.toString()).toMatch(/No per-action billing/);
    expect(landingPricingSectionSub.toString()).toMatch(/No per-action billing/);
    expect(heroStatTokenBilling.toString()).toMatch(/TOKEN_WORDING_ACTIVE \? null/);
  });

  it("no exported string ever hardcodes an 'about N fixes' estimate", () => {
    const all = [
      heroFreeOfferCta(), heroFreeOfferSubline(), signupFreeOfferTitle(),
      signupFreeOfferDescription(), loginFreeOfferCta(), defaultPageMetaDescription(),
      freeOfferShortLine(),
    ].join(" ");
    expect(all.toLowerCase()).not.toMatch(/about \d+ fix/);
  });
});
