/**
 * Landing.chooserStrip.test.jsx — homepage chooser strip (2026-11-xx,
 * draft, flag default OFF, same `harness_page` flag as the Aurem
 * Loop promo section). Same source-grep convention as
 * Landing.harnessPromo.test.jsx — Landing.jsx is 1500+ lines and a
 * full React Testing Library render pulls in a large amount of
 * unrelated mocking, so structural assertions on the source are the
 * established pattern here.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "Landing.jsx"), "utf-8");

describe("Landing.jsx — homepage chooser strip", () => {
  it("renders only when harnessPageEnabled is true (same flag/state as the promo section, no second fetch)", () => {
    const stripBlock = SRC.match(/\{harnessPageEnabled && \(\s*<nav[\s\S]*?<\/nav>\s*\)\}/);
    expect(stripBlock, "chooser strip must be gated by {harnessPageEnabled && (...)}").toBeTruthy();
  });

  it("has exactly one ORA by AUREM pill (active/current page) and one AUREM LOOP harness pill linking to /harness", () => {
    expect(SRC).toMatch(/data-testid="chooser-pill-ora"[\s\S]{0,40}aria-current="page"[\s\S]{0,20}>\s*ORA by AUREM/);
    expect(SRC).toMatch(/<Link\s+className="chooser-pill"\s+to="\/harness"\s+data-testid="chooser-pill-harness"\s*>\s*AUREM LOOP harness/);
  });

  it("is a quiet pill row with its own test id, living inside the hero (not a second hero, not a separate strip above it)", () => {
    expect(SRC).toMatch(/data-testid="homepage-chooser-strip"/);
    expect(SRC).toMatch(/className="chooser-strip chooser-strip-in-hero"/);
  });

  it("sits inside the hero section, right after the hero-badge, before the H1", () => {
    const heroBlock = SRC.match(/<section className="hero">[\s\S]*?<\/section>/);
    expect(heroBlock, "hero section not found").toBeTruthy();
    const hero = heroBlock[0];
    const badgeIdx = hero.indexOf('className="hero-badge"');
    const stripIdx = hero.indexOf('data-testid="homepage-chooser-strip"');
    const h1Idx = hero.indexOf('data-testid="hero-headline"');
    expect(badgeIdx).toBeGreaterThan(-1);
    expect(stripIdx).toBeGreaterThan(badgeIdx);
    expect(h1Idx).toBeGreaterThan(stripIdx);
  });

  it("the /both route has been retired, not just hidden", () => {
    expect(fs.existsSync(path.resolve(__dirname, "..", "Both.jsx"))).toBe(false);
  });
});
