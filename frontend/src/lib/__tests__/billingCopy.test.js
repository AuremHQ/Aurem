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

  it("no exported string ever hardcodes an 'about N fixes' estimate", () => {
    const all = [
      heroFreeOfferCta(), heroFreeOfferSubline(), signupFreeOfferTitle(),
      signupFreeOfferDescription(), loginFreeOfferCta(), defaultPageMetaDescription(),
      freeOfferShortLine(),
    ].join(" ");
    expect(all.toLowerCase()).not.toMatch(/about \d+ fix/);
  });
});
