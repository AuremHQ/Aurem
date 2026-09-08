/**
 * MessageBubble.p5.test.jsx — 2026-08-27, P5 (Journey/Intent-Grounding
 * build round). Confirmed engine-leak fixes:
 *   1. A raw ```aurem-handoff fence rendering verbatim in the chat
 *      stream (its only job is driving the Ship button, never meant
 *      to be visible prose).
 */
import { describe, it, expect } from "vitest";
import { stripHandoffFenceForDisplay } from "../MessageBubble.jsx";

describe("2026-08-27 P5 · aurem-handoff fence never renders as visible text", () => {
  it("strips a real aurem-handoff fence, keeping surrounding prose", () => {
    const raw = [
      "I read the file and found the bug.",
      "```aurem-handoff",
      "Fix the null check in Signup.jsx line 42.",
      "```",
    ].join("\n");
    const shown = stripHandoffFenceForDisplay(raw);
    expect(shown).not.toContain("aurem-handoff");
    expect(shown).not.toContain("Fix the null check");
    expect(shown).toContain("I read the file and found the bug.");
  });

  it("leaves normal content (no fence) completely untouched", () => {
    const raw = "Just a normal reply with no handoff block.";
    expect(stripHandoffFenceForDisplay(raw)).toBe(raw);
  });

  it("strips multiple aurem-handoff fences if present", () => {
    const raw = "A\n```aurem-handoff\nX\n```\nB\n```aurem-handoff\nY\n```\nC";
    const shown = stripHandoffFenceForDisplay(raw);
    expect(shown).not.toContain("aurem-handoff");
    expect(shown).toContain("A");
    expect(shown).toContain("B");
    expect(shown).toContain("C");
  });

  // 2026-09-11 — real bug repro (TC-04, founder screenshot): a reply
  // that is JUST the fence, no surrounding prose, left the bubble
  // body completely blank (only badges/attribution showed) after
  // stripping. Must fall back to an honest one-liner instead.
  it("t_no_prose_fence_shows_fallback_not_blank: a fence-only reply (no prose at all) shows a fallback line instead of an empty body", () => {
    const raw = "```aurem-handoff\nAdd `// QA regression test marker` to the top of README.md.\n```";
    const shown = stripHandoffFenceForDisplay(raw);
    expect(shown).not.toBe("");
    expect(shown).not.toContain("aurem-handoff");
    expect(shown.length).toBeGreaterThan(0);
  });

  it("t_fence_only_with_whitespace_padding_still_falls_back: leading/trailing whitespace around a fence-only reply still triggers the fallback", () => {
    const raw = "\n\n```aurem-handoff\nDo the thing.\n```\n\n";
    const shown = stripHandoffFenceForDisplay(raw);
    expect(shown).not.toBe("");
  });

  it("t_fence_with_prose_never_falls_back: a fence WITH real surrounding prose keeps showing that prose, not the fallback line", () => {
    const raw = "Done — added the comment.\n```aurem-handoff\nAdd a comment.\n```";
    const shown = stripHandoffFenceForDisplay(raw);
    expect(shown).toBe("Done — added the comment.");
  });
});
