/**
 * competitors.tokenWording.test.js — homepage token-billing cutover,
 * extended to /vs + /compare (2026-11-08).
 *
 * src/data/competitors.mjs is imported by BOTH the Vite-bundled React
 * pages (VsPage.jsx, CompareHub.jsx — reads `import.meta.env`) AND
 * the plain-Node scripts/seo-prerender.mjs (reads `process.env`), so
 * its own `TOKEN_WORDING_ACTIVE` checks both. In this Vitest/jsdom
 * env `import.meta.env.VITE_TOKEN_BILLING_MODE` is always unset
 * (same as billingCopy.test.js's own default-false assertion), so
 * setting `process.env.VITE_TOKEN_BILLING_MODE` before a cache-busted
 * dynamic import exercises the Node fallback branch exactly as
 * scripts/seo-prerender.mjs does at build time.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

const importFresh = async () => {
  vi.resetModules();
  return import("../competitors.mjs");
};

describe("competitors.mjs — token wording gate", () => {
  afterEach(() => {
    delete process.env.VITE_TOKEN_BILLING_MODE;
  });

  it("defaults to legacy task-based wording when the flag is unset", async () => {
    const m = await importFresh();
    expect(m.TOKEN_WORDING_ACTIVE).toBe(false);
    expect(m.AUREM.pricing).toMatch(/50 tasks.*300 tasks/);
    expect(m.AUREM.freeTier).toBe("10 tasks/month, no credit card");
  });

  it("switches every AUREM-side claim to token wording when VITE_TOKEN_BILLING_MODE=on", async () => {
    process.env.VITE_TOKEN_BILLING_MODE = "on";
    const m = await importFresh();
    expect(m.TOKEN_WORDING_ACTIVE).toBe(true);
    expect(m.AUREM.pricing).toMatch(/900 tokens.*1,900 tokens.*4,900 tokens/);
    expect(m.AUREM.freeTier).toBe("One free full repo scan, no credit card");

    // Full-object sweep: no competitor entry may still say "50 tasks",
    // "300 tasks", "10 tasks/month" or falsely claim AUREM bills
    // "not for tokens" once token mode is active.
    const blob = JSON.stringify(m.COMPETITORS) + JSON.stringify(m.AUREM);
    const forbidden = [
      "50 tasks", "300 tasks", "10 tasks/month", "10 tasks per month",
      "not for tokens", "no token metering", "no token billing",
      "flat tasks", "task outcomes",
    ];
    forbidden.forEach((phrase) => expect(blob).not.toContain(phrase));
  });

  it("falls back to legacy wording for any unrecognised flag value (fail-safe)", async () => {
    process.env.VITE_TOKEN_BILLING_MODE = "nonsense";
    const m = await importFresh();
    expect(m.TOKEN_WORDING_ACTIVE).toBe(false);
    expect(m.AUREM.freeTier).toBe("10 tasks/month, no credit card");
  });
});
