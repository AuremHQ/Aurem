/**
 * draftReviewHelpers.truncateAtWord.test.js — UI-fix C10 (2026-10)
 *
 * Bug fix: the draft title hard-sliced at exactly 100 chars, cutting
 * mid-word (audit saw "...notificati"). truncateAtWord backs up to
 * the last word boundary before the limit instead.
 */
import { describe, it, expect } from "vitest";
import { truncateAtWord } from "../draftReviewHelpers";

describe("UI-fix C10 · truncateAtWord", () => {
  it("cuts at the last space before the limit, not mid-word", () => {
    const brief = "A habit tracker that sends me a push notification every single morning with my streak and today's planned workout notifications";
    const out = truncateAtWord(brief, 100);
    expect(out.endsWith("…")).toBe(true);
    expect(out).not.toContain("notificati…"); // the old bug's exact cut
    const withoutEllipsis = out.slice(0, -1);
    expect(brief.startsWith(withoutEllipsis)).toBe(true);
    expect(withoutEllipsis.endsWith(" ")).toBe(false); // trimmed, no trailing space before …
  });

  it("leaves short text untouched, no ellipsis", () => {
    expect(truncateAtWord("A short brief", 100)).toBe("A short brief");
  });

  it("handles empty/undefined input", () => {
    expect(truncateAtWord("", 100)).toBe("");
    expect(truncateAtWord(undefined, 100)).toBe("");
  });
});
