/**
 * companyInfo.tokenWording.test.js — homepage token-billing cutover,
 * extended to /about (2026-11-08). Locks in the fix for the About
 * page's own stale claim ("Pricing is flat, not per-token") which
 * was directly contradictory once token mode went live. Same
 * cache-busted-dynamic-import pattern as competitors.tokenWording.test.js.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

const importFresh = async () => {
  vi.resetModules();
  return import("../companyInfo.mjs");
};

describe("companyInfo.mjs — token wording gate", () => {
  afterEach(() => {
    delete process.env.VITE_TOKEN_BILLING_MODE;
  });

  it("defaults to the legacy 'not per-token' claim when the flag is unset", async () => {
    const m = await importFresh();
    expect(m.TOKEN_WORDING_ACTIVE).toBe(false);
    const about = m.ABOUT.paragraphs.join(" ");
    expect(about).toMatch(/Pricing is flat, not per-token/);
    expect(about).toMatch(/10 tasks\/month, no card/);
  });

  it("drops the 'not per-token' claim and states the token balance honestly when token mode is on", async () => {
    process.env.VITE_TOKEN_BILLING_MODE = "on";
    const m = await importFresh();
    expect(m.TOKEN_WORDING_ACTIVE).toBe(true);
    const about = m.ABOUT.paragraphs.join(" ");
    expect(about).not.toMatch(/not per-token/);
    expect(about).not.toMatch(/10 tasks\/month, no card/);
    expect(about).toMatch(/900 tokens/);
    expect(about).toMatch(/one full scan, no card/);
  });

  it("falls back to legacy wording for any unrecognised flag value (fail-safe)", async () => {
    process.env.VITE_TOKEN_BILLING_MODE = "nonsense";
    const m = await importFresh();
    expect(m.TOKEN_WORDING_ACTIVE).toBe(false);
  });
});
