/**
 * MasterPromptV2Part5_marketing_tokenWording.test.jsx
 *
 * Master Prompt v2 PART 5.1/5.2 — one consolidated source-inspection
 * test (same convention as Settings.track_switcher_hidden.test.js)
 * for every marketing/auth page touched this turn: each must import
 * the shared billingCopy module and gate its "free tasks"/CTA copy
 * behind TOKEN_WORDING_ACTIVE rather than hardcoding the legacy
 * string unconditionally.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

function src(...parts) {
  return fs.readFileSync(path.resolve(__dirname, "..", ...parts), "utf-8");
}

describe("Marketing/auth pages import the shared billingCopy module", () => {
  it("Signup.jsx", () => {
    const s = src("Signup.jsx");
    expect(s).toMatch(/from "\.\.\/lib\/billingCopy"/);
    expect(s).toMatch(/title: signupFreeOfferTitle\(\)/);
    expect(s).toMatch(/description: signupFreeOfferDescription\(\)/);
    expect(s).toMatch(/\{freeOfferShortLine\(\)\}/);
  });

  it("Login.jsx", () => {
    const s = src("Login.jsx");
    expect(s).toMatch(/from "\.\.\/lib\/billingCopy"/);
    expect(s).toMatch(/\{loginFreeOfferCta\(\)\}/);
  });

  it("WhyOra.jsx", () => {
    const s = src("WhyOra.jsx");
    expect(s).toMatch(/TOKEN_WORDING_ACTIVE.*lib\/billingCopy/);
    expect(s).toMatch(/TOKEN_WORDING_ACTIVE \? "A free full scan\. No credit card\." : "10 free tasks\. No credit card\."/);
  });

  it("VsPage.jsx", () => {
    const s = src("VsPage.jsx");
    expect(s).toMatch(/TOKEN_WORDING_ACTIVE.*lib\/billingCopy/);
    expect(s).toMatch(/TOKEN_WORDING_ACTIVE \? "A free full scan\. No credit card\." : "10 free tasks every month\. No credit card\."/);
  });

  it("CompareHub.jsx", () => {
    const s = src("CompareHub.jsx");
    expect(s).toMatch(/from "\.\.\/lib\/billingCopy"/);
    expect(s).toMatch(/\{heroFreeOfferCta\(\)\}/);
  });
});

describe("demoSteps.jsx imports the shared billingCopy module", () => {
  it("gates the free-offer caption/line behind TOKEN_WORDING_ACTIVE", () => {
    const s = fs.readFileSync(
      path.resolve(__dirname, "..", "..", "components", "demo", "demoSteps.jsx"), "utf-8",
    );
    expect(s).toMatch(/from "\.\.\/\.\.\/lib\/billingCopy"/);
    expect(s).toMatch(/\{freeOfferShortLine\(\)\}/);
    expect(s).toMatch(/TOKEN_WORDING_ACTIVE \? "Sign up in seconds/);
  });
});

describe("usePageMeta.js default description comes from billingCopy, not a hardcoded literal", () => {
  it("imports defaultPageMetaDescription", () => {
    const s = fs.readFileSync(path.resolve(__dirname, "..", "..", "lib", "usePageMeta.js"), "utf-8");
    expect(s).toMatch(/import \{ defaultPageMetaDescription \} from "\.\/billingCopy"/);
    expect(s).toMatch(/const DEFAULT_DESC = defaultPageMetaDescription\(\);/);
    expect(s).not.toMatch(/"AUREM is an autonomous AI engineer.*10 free tasks/);
  });
});
