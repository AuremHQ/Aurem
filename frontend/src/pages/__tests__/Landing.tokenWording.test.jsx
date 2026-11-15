/**
 * Landing.tokenWording.test.jsx — Master Prompt v2 PART 5.1/5.2.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "Landing.jsx"), "utf-8");

describe("Landing.jsx — Master Prompt v2 PART 5.1 token wording gate", () => {
  it("hero + bottom CTAs read from the shared billingCopy module", () => {
    // 2026-10-25 (ITEM 6, independent review) — widened from an exact
    // two-name match to tolerate the sibling imports ITEM 6 added to
    // this same line (heroTokenMetersClaim/landingPricingSectionSub/
    // heroStatTokenBilling) without weakening the actual assertion:
    // both names must still come from billingCopy, in one import.
    const importLine = SRC.match(/import \{[^}]*\} from "\.\.\/lib\/billingCopy";/)?.[0];
    expect(importLine, "no import from ../lib/billingCopy found").toBeTruthy();
    expect(importLine).toMatch(/\bheroFreeOfferCta\b/);
    expect(importLine).toMatch(/\bTOKEN_WORDING_ACTIVE\b/);
    expect(SRC).toMatch(/\{heroFreeOfferCta\(\)\}/g);
  });

  it("the hero pill and bottom cta-sub swap wording based on the flag, not unconditional", () => {
    expect(SRC).toMatch(/TOKEN_WORDING_ACTIVE \? "A free full scan" : "10 free tasks"/);
    expect(SRC).toMatch(/TOKEN_WORDING_ACTIVE \? "Start free\. One full scan\. No credit card required\." : "Start free\. 10 tasks\. No credit card required\."/);
  });

  // 2026-10-27 — testing-agent finding: the tolerant import-line regex
  // above missed a hardcoded marquee string ("Flat $9/mo · no token
  // meter") that never went through billingCopy.js at all — it just
  // silently rendered unconditionally regardless of the flag. This is
  // a blunter, static-grep-of-the-WHOLE-SOURCE check specifically so
  // a *new* hardcoded "no token meter(s)"/"task(s) free"/"tasks/month"
  // string anywhere in this 1400+ line file can never slip back in
  // unnoticed the way this one did — every occurrence of these
  // forbidden phrases must be inside a `TOKEN_WORDING_ACTIVE ? ... :`
  // ternary (so the legacy branch is allowed, the unconditional
  // version is not).
  it("no hardcoded 'no token meter(s)' string exists outside a TOKEN_WORDING_ACTIVE ternary", () => {
    const forbidden = /no token meters?\b/gi;
    let match;
    while ((match = forbidden.exec(SRC)) !== null) {
      const before = SRC.slice(Math.max(0, match.index - 80), match.index);
      expect(before, `found "${match[0]}" with no preceding TOKEN_WORDING_ACTIVE ternary nearby (context: ...${before.slice(-60)})`)
        .toMatch(/TOKEN_WORDING_ACTIVE \? [^:]*:\s*"[^"]*$/);
    }
  });

  it("the marquee's flat-price tagline is gated, not hardcoded", () => {
    expect(SRC).toMatch(/TOKEN_WORDING_ACTIVE \? "Flat \$9\/mo · 900 tokens" : "Flat \$9\/mo · no token meter"/);
  });
});
