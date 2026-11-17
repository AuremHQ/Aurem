/**
 * harnessContent.driftGuard.test.js — regression guard for the
 * 2026-11-17 SSR-staleness bug: scripts/seo-prerender.mjs used to
 * build the static /harness crawler snapshot from a now-deleted
 * content-doc fetch (HARNESS_CONTENT, obsolete `content.hero` /
 * `content.setApart` / `content.faq` shape). That left the snapshot
 * permanently stale after HarnessPage.jsx was rebuilt around
 * src/data/harnessContent.mjs, and would throw a ReferenceError on
 * HARNESS_CONTENT once that variable was removed. This test does not
 * execute seo-prerender.mjs (it has build-time side effects — fetches
 * a live backend URL, writes to dist/) — it asserts the fix at the
 * two points that matter: the shared module's exports are present and
 * well-formed, and the script's source draws from that exact module
 * instead of the deleted one.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import * as harnessContent from "../harnessContent.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PRERENDER_SRC = readFileSync(
  join(HERE, "..", "..", "..", "scripts", "seo-prerender.mjs"),
  "utf-8",
);

describe("harnessContent.mjs — shared /harness content module", () => {
  it("exports every section the live page and the SSR snapshot both need", () => {
    expect(harnessContent.HERO.sub).toBeTruthy();
    expect(harnessContent.POINTS.length).toBeGreaterThan(0);
    expect(harnessContent.FEAT_ITEMS.length).toBeGreaterThan(0);
    expect(harnessContent.FLOW_STEPS.length).toBeGreaterThan(0);
    expect(harnessContent.REQUIREMENTS.length).toBeGreaterThan(0);
    expect(harnessContent.FAQ_ITEMS.length).toBeGreaterThan(0);
    expect(harnessContent.LEGAL_ARTICLES.map((a) => a.id)).toEqual([
      "license",
      "privacy",
      "terms",
    ]);
    expect(harnessContent.FOOTER_COLS.length).toBeGreaterThan(0);
    expect(harnessContent.FOOTER_TAGLINE).toBeTruthy();
    expect(harnessContent.FOOTER_COPY).toBeTruthy();
    expect(harnessContent.FOOTER_TRADEMARK).toBeTruthy();
  });

  it("every FAQ/legal HTML field is a non-empty string (dangerouslySetInnerHTML target)", () => {
    harnessContent.FAQ_ITEMS.forEach((f) => expect(typeof f.aHtml).toBe("string"));
    harnessContent.LEGAL_ARTICLES.forEach((a) => expect(typeof a.bodyHtml).toBe("string"));
  });
});

describe("seo-prerender.mjs — /harness snapshot drift guard", () => {
  it("imports its /harness content from the same shared module HarnessPage.jsx uses", () => {
    expect(PRERENDER_SRC).toMatch(/from ["']\.\.\/src\/data\/harnessContent\.mjs["']/);
  });

  it("no longer references the deleted content-doc fetch (the actual bug that shipped stale snapshots)", () => {
    expect(PRERENDER_SRC).not.toMatch(/HARNESS_CONTENT\b/);
    expect(PRERENDER_SRC).not.toMatch(/resolveHarnessContent\b/);
  });

  it("still gates the /harness snapshot on the live harness_page flag, and reads the real release for version/sha", () => {
    expect(PRERENDER_SRC).toMatch(/HARNESS_PAGE_MODE/);
    expect(PRERENDER_SRC).toMatch(/harness\/release-status/);
  });
});
