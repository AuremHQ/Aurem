/**
 * Regression (iter 2026-70): Landing.jsx and lib/billingCopy.js on main
 * were corrupted (pasted model text / truncated), so `vite build`
 * failed. This renders the real Landing page and calls the real
 * billingCopy exports, so a corrupt file fails here, not at deploy.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({ data: {} })) },
  healthApi: { get: vi.fn(() => Promise.resolve({ data: {} })) },
}));
vi.mock("../../components/PricingCards", () => ({ default: () => <div /> }));
vi.mock("../../components/LazyVideo", () => ({ default: () => <div /> }));
vi.mock("../../components/marketing/MarketingNav", () => ({ default: () => <div /> }));
vi.mock("../../components/demo/WalkthroughPlayer", () => ({ default: () => <div /> }));
vi.mock("../../components/demo/demoSteps", () => ({ FULL_STEPS: [] }));

import Landing from "../Landing";
import * as copy from "../../lib/billingCopy";

describe("Landing renders from the real source", () => {
  it("shows a non-empty hero headline", () => {
    render(<MemoryRouter><Landing /></MemoryRouter>);
    expect(screen.getByTestId("hero-headline").textContent.length).toBeGreaterThan(5);
  });
});

describe("billingCopy exports work", () => {
  it("every copy helper returns a non-empty string", () => {
    const fns = ["heroFreeOfferCta", "heroFreeOfferSubline", "heroTokenMetersClaim",
      "comparisonSectionSub", "comparisonFlatPricingRowLabel", "landingPricingSectionSub",
      "pricingHeroHeadline"];
    for (const n of fns) {
      expect(typeof copy[n], n).toBe("function");
      const v = copy[n]();
      expect(typeof v, n).toBe("string");
      expect(v.length, n).toBeGreaterThan(0);
    }
    expect(typeof copy.TOKEN_WORDING_ACTIVE).toBe("boolean");
  });

  it("plan token amounts are positive numbers", () => {
    for (const v of Object.values(copy.PLAN_TOKENS)) expect(v).toBeGreaterThan(0);
  });
});
