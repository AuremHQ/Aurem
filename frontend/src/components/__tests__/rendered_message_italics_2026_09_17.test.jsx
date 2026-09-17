/**
 * 2026-09-17 — Founder-reported: single-underscore markdown italics
 * (`_..._`) rendered as literal underscores in chat bubbles, e.g.
 * `_0 pages scanned · 0 critical · 1 medium · 1 low_` showed the raw
 * underscore characters instead of italic text. `renderInline` only
 * ever recognised backtick-code and `**bold**` — no italic support
 * at all. Fixed by adding an `_..._` → <em> branch, guarded so
 * `snake_case_identifiers` in prose are never mistaken for emphasis.
 */
import { describe, it, expect } from "vitest";
import React from "react";
import { render } from "@testing-library/react";
import RenderedMessage from "../RenderedMessage.jsx";

describe("RenderedMessage — _italic_ emphasis", () => {
  it("renders the exact reported scan-summary line as italic text, no literal underscores", () => {
    const md = "_0 pages scanned · 0 critical · 0 high · 1 medium · 1 low_";
    const { container } = render(<RenderedMessage text={md} />);
    const em = container.querySelector("em");
    expect(em).toBeTruthy();
    expect(em.textContent).toBe(
      "0 pages scanned · 0 critical · 0 high · 1 medium · 1 low",
    );
    expect(container.textContent).not.toMatch(/_/);
  });

  it("renders a mid-sentence italic phrase without swallowing surrounding text", () => {
    const md = "2 of these can be auto-fixed. Say _fix the critical issues_ to proceed.";
    const { container } = render(<RenderedMessage text={md} />);
    const em = container.querySelector("em");
    expect(em).toBeTruthy();
    expect(em.textContent).toBe("fix the critical issues");
    expect(container.textContent).toMatch(/^2 of these can be auto-fixed\. Say fix the critical issues to proceed\.$/);
  });

  it("does NOT italicize snake_case identifiers in prose", () => {
    const md = "Check the `preview_url` field on the project_id record.";
    const { container } = render(<RenderedMessage text={md} />);
    expect(container.querySelector("em")).toBeNull();
    expect(container.textContent).toMatch(/project_id/);
  });

  it("still renders **bold** and `code` alongside _italic_ in the same line", () => {
    const md = "**Bold**, `code`, and _italic_ all in one line.";
    const { container } = render(<RenderedMessage text={md} />);
    expect(container.querySelector("strong")?.textContent).toBe("Bold");
    expect(container.querySelector("code")?.textContent).toBe("code");
    expect(container.querySelector("em")?.textContent).toBe("italic");
  });

  // 2026-09-17 — founder found this DURING live verification of the
  // _italic_ fix above: `**bold**` NESTED inside an `_italic_` span
  // rendered the bold markers as literal asterisks (the exact
  // reported case: `_Say **"fix the critical issues"** to proceed._`
  // in a scan-report follow-up). Root cause: <em> rendered its inner
  // text as a plain string, never re-parsed for the bold syntax
  // nested inside it. Fixed by recursing renderInline on the
  // stripped-delimiter inner content.
  it("renders **bold** nested inside an _italic_ span — the exact founder-reported case", () => {
    const md = '2 of these can be auto-fixed. _Say **"fix the critical issues"** to proceed._';
    const { container } = render(<RenderedMessage text={md} />);
    const em = container.querySelector("em");
    expect(em).toBeTruthy();
    const strong = em.querySelector("strong");
    expect(strong).toBeTruthy();
    expect(strong.textContent).toBe('"fix the critical issues"');
    // No literal markdown markers should leak through anywhere.
    expect(container.textContent).not.toMatch(/\*/);
    expect(container.textContent).not.toMatch(/_/);
  });

  it("renders _italic_ nested inside a **bold** span (the reverse nesting direction)", () => {
    const md = "This is **bold with _italic_ inside it** here.";
    const { container } = render(<RenderedMessage text={md} />);
    const strong = container.querySelector("strong");
    expect(strong).toBeTruthy();
    const em = strong.querySelector("em");
    expect(em).toBeTruthy();
    expect(em.textContent).toBe("italic");
    expect(container.textContent).not.toMatch(/\*/);
    expect(container.textContent).not.toMatch(/_/);
  });
});
