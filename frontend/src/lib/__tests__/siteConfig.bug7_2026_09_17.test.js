/**
 * Bug 7 fix (2026-09-17) — page title flipped between 3 different
 * hand-typed variants across navigations ("developers choice" set by
 * Landing.jsx and never restored, index.html's static default, and
 * usePageMeta.js's own third DEFAULT_TITLE). One shared source of
 * truth now: lib/siteConfig.js.
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";
import { SITE, pageTitle } from "../siteConfig";

describe("siteConfig — single source of truth for the page title (Bug 7 fix)", () => {
  it("pageTitle() with no section matches index.html's static <title> byte-for-byte", () => {
    const indexHtml = fs.readFileSync(
      path.resolve(__dirname, "../../../index.html"), "utf-8",
    );
    const m = /<title>([^<]+)<\/title>/.exec(indexHtml);
    expect(m).toBeTruthy();
    expect(pageTitle()).toBe(m[1]);
  });

  it("pageTitle(section) templates the brand name consistently", () => {
    expect(pageTitle("Developers")).toBe(`Developers — ${SITE.name}`);
  });

  it("does not contain the old 'developers choice' variant anywhere", () => {
    expect(pageTitle()).not.toMatch(/developers choice/i);
    expect(SITE.tagline).not.toMatch(/developers choice/i);
  });
});
