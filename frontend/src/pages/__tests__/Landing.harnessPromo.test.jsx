/**
 * Landing.harnessPromo.test.jsx — "Aurem Loop" homepage promo section
 * (2026-11-09, draft, flag default OFF). Same source-grep convention
 * as Landing.tokenWording.test.jsx — this file is 1500+ lines and a
 * full React Testing Library render pulls in a large amount of
 * unrelated mocking, so structural assertions on the source are the
 * established pattern here.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "Landing.jsx"), "utf-8");

describe("Landing.jsx — Aurem Loop promo section", () => {
  it("fetches the public, no-auth harness/page-status endpoint directly (not gated via pageFlags.js's auth-dependent mirror)", () => {
    expect(SRC).toMatch(/healthApi\.get\(["']\/harness\/page-status["']\)/);
    // The promo section's conditional render must read the local
    // `harnessPageEnabled` state set by that fetch, not pageFlags.js.
    expect(SRC).toMatch(/\{harnessPageEnabled && \(/);
  });

  it("defaults harnessPageEnabled to false and only flips true on an explicit true response", () => {
    expect(SRC).toMatch(/useState\(false\);\s*\n\s*useEffect\(\(\) => \{\s*\n\s*healthApi\.get\(["']\/harness\/page-status["']\)/);
    expect(SRC).toMatch(/r\.data\?\.harness_page_enabled === true/);
  });

  it("fails closed on any fetch error (network failure, 404, timeout) — never shows by mistake", () => {
    const fetchBlock = SRC.match(/healthApi\.get\(["']\/harness\/page-status["'][\s\S]{0,300}?\.catch\(\(\) => \{\}\);/);
    expect(fetchBlock, "harness/page-status fetch has no .catch(() => {}) fail-closed handler").toBeTruthy();
  });

  it("renders the section only when harnessPageEnabled is true (conditional, not unconditional)", () => {
    expect(SRC).toMatch(/\{harnessPageEnabled && \(/);
  });

  it("uses the exact founder-approved heading and CTA copy, linking to /harness", () => {
    expect(SRC).toMatch(/data-testid="aurem-loop-promo-heading">Aurem Loop</);
    expect(SRC).toMatch(/data-testid="aurem-loop-promo-cta">Learn about Aurem Loop →/);
    expect(SRC).toMatch(/<Link className="btn-ghost" to="\/harness" data-testid="aurem-loop-promo-cta">/);
  });

  // 2026-11-16 — launch copy fix. The line used to be a static
  // "Not available to download yet." even after v0.8.0 shipped —
  // contradicting the live, downloadable /harness page. It now reads
  // the same /harness/release-status the /harness page itself uses.
  describe("release-aware promo line (launch copy fix, 2026-11-16)", () => {
    it("fetches /harness/release-status and never hardcodes a version number", () => {
      expect(SRC).toMatch(/healthApi\.get\(["']\/harness\/release-status["']\)/);
      expect(SRC).not.toMatch(/v0\.8\.0/);
    });

    it("shows the stale 'not available yet' line when no release is published", () => {
      expect(SRC).toMatch(/"A local coding harness that checks its own work\. Not available to download yet\."/);
    });

    it("shows a download-available line with the live version when a release is published", () => {
      expect(SRC).toMatch(/harnessRelease\.available/);
      expect(SRC).toMatch(/Download available — v\$\{harnessRelease\.version\}/);
    });

    it("renders a 'Download Aurem Loop' CTA to /harness only when a release is available", () => {
      expect(SRC).toMatch(/data-testid="aurem-loop-promo-download-cta"/);
      const downloadCtaBlock = SRC.match(/\{harnessRelease\.available && \([\s\S]*?aurem-loop-promo-download-cta[\s\S]*?\)\}/);
      expect(downloadCtaBlock, "download CTA is not conditionally gated on harnessRelease.available").toBeTruthy();
    });
  });

  it("the promo copy makes no Ollama/local-mode claim, benchmark numbers, Loop C edit claim, or working-cloud-option claim", () => {
    const promoSection = SRC.match(/\{harnessPageEnabled && \([\s\S]*?\)\}/)?.[0] ?? "";
    expect(promoSection).not.toMatch(/ollama/i);
    expect(promoSection).not.toMatch(/\d+%|\d+x faster|benchmark/i);
    expect(promoSection).not.toMatch(/loop c/i);
    expect(promoSection).not.toMatch(/cloud/i);
  });
});
