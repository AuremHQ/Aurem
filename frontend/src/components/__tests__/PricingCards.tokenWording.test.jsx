/**
 * PricingCards.tokenWording.test.jsx — Master Prompt v2 PART 5.2.
 * Confirms PRICING_TIERS' per-tier capacity bullet uses legacy task
 * wording today and swaps to token wording when billingCopy reports
 * TOKEN_WORDING_ACTIVE (mocked here — this is a pure data-shape test,
 * not an env-var integration test, which lib/billingCopy.test.js
 * already covers).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

describe("PricingCards.PRICING_TIERS — legacy mode (real billingCopy, unmocked)", () => {
  it("shows the old task-based bullets today", async () => {
    vi.resetModules();
    const { PRICING_TIERS } = await import("../PricingCards.jsx");
    const free = PRICING_TIERS.find((t) => t.id === "free");
    const starter = PRICING_TIERS.find((t) => t.id === "starter");
    const team = PRICING_TIERS.find((t) => t.id === "team");
    expect(free.features).toContain("10 tasks per month");
    expect(starter.features).toContain("50 tasks per month");
    expect(team.features).toContain("400 tasks per month");
  });
});

describe("PricingCards.PRICING_TIERS — token-mode wording (billingCopy mocked)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("swaps each tier's capacity bullet for the token-wording variant", async () => {
    vi.doMock("../../lib/billingCopy", () => ({
      tierCapacityLine: (tierId) => {
        const map = { free: "1 free full scan", starter: "900 tokens / month", pro: "1,900 tokens / month", team: "4,900 tokens / month" };
        return map[tierId] || null;
      },
    }));
    const { PRICING_TIERS } = await import("../PricingCards.jsx");
    const free = PRICING_TIERS.find((t) => t.id === "free");
    const starter = PRICING_TIERS.find((t) => t.id === "starter");
    const team = PRICING_TIERS.find((t) => t.id === "team");

    expect(free.features).toContain("1 free full scan");
    expect(free.features).not.toContain("10 tasks per month");
    expect(starter.features).toContain("900 tokens / month");
    expect(starter.features).not.toContain("50 tasks per month");
    expect(team.features).toContain("4,900 tokens / month");
    expect(team.features).not.toContain("400 tasks per month");
    // No feature line anywhere hardcodes an "about N fixes" estimate.
    const allFeatures = PRICING_TIERS.flatMap((t) => t.features).join(" ");
    expect(allFeatures.toLowerCase()).not.toMatch(/about \d+ fix/);
  });
});
