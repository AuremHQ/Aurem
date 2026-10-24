/**
 * Landing.tokenWording.test.jsx — Master Prompt v2 PART 5.1/5.2.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "Landing.jsx"), "utf-8");

describe("Landing.jsx — Master Prompt v2 PART 5.1 token wording gate", () => {
  it("hero + bottom CTAs read from the shared billingCopy module", () => {
    expect(SRC).toMatch(/import \{ heroFreeOfferCta, TOKEN_WORDING_ACTIVE \} from "\.\.\/lib\/billingCopy"/);
    expect(SRC).toMatch(/\{heroFreeOfferCta\(\)\}/g);
  });

  it("the hero pill and bottom cta-sub swap wording based on the flag, not unconditional", () => {
    expect(SRC).toMatch(/TOKEN_WORDING_ACTIVE \? "A free full scan" : "10 free tasks"/);
    expect(SRC).toMatch(/TOKEN_WORDING_ACTIVE \? "Start free\. One full scan\. No credit card required\." : "Start free\. 10 tasks\. No credit card required\."/);
  });
});
