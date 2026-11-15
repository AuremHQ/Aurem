/**
 * MessageBubble.ship_banner_audit_footer.test.jsx
 *
 * BUG (reported): "The approve button didn't load for that fix" red
 * banner fires on every Mode E audit/scan reply that has fixable
 * items, even though that reply has no fence and no button by
 * design. Root cause: `SHIP_CTA_MENTION`'s `ship (it|this|the)`
 * alternative has no word boundaries, so "ship the" matches as a
 * literal substring prefix of "ship them" — and
 * `backend/services/mode_e_auditor.py::build_audit_report()`'s own
 * footer ends with "...and I'll ship them via Mode C." on every scan
 * that found a fixable issue.
 *
 * STEP 1 (TDD red-first): these assertions are expected to FAIL
 * against the pre-fix code and PASS after STEP 2's regex +
 * provider-skip fix.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import MessageBubble, { detectShipCtaFallback } from "../MessageBubble.jsx";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn(() => Promise.resolve({ data: {} })) },
  getToken: () => null,
}));
vi.mock("../Toast", () => ({ toast: vi.fn() }));

const ACTIVE_PROJECT = {
  project_id: "proj_1", github_owner: "acme", github_repo: "widgets", branch: "main",
};

// Exact text shape produced by backend/services/mode_e_auditor.py::build_audit_report()
// for a scan that found a critical + a high issue, both fixable.
const REAL_AUDIT_FOOTER_CRITICAL = [
  "**Audit report — acme/widgets**",
  "_3 file(s) deep-scanned · 1 critical · 1 high · 0 medium · 0 low_",
  "",
  "🔴 **Critical issues** (1)",
  "- `app.py:10` — SQL injection via string formatting",
  "  → Fix: use parameterized queries",
  "",
  "🟠 **High priority** (1)",
  "- `app.py:20` — Reflected XSS in template",
  "  → Fix: escape output",
  "",
  '\n_2 of these can be auto-fixed. Say **"fix the critical issues"** and I\'ll ship them via Mode C._',
].join("\n");

// Same shape, but only low-severity fixable items (exercises the
// "low-priority items" label branch of build_audit_report()).
const REAL_AUDIT_FOOTER_LOW = [
  "**Audit report — acme/widgets**",
  "_2 file(s) deep-scanned · 0 critical · 0 high · 0 medium · 1 low_",
  "",
  "🟡 **Low priority** (1)",
  "- `README.md` — missing license line",
  "  → Fix: add a license line",
  "",
  '\n_1 of these can be auto-fixed. Say **"fix the low-priority items"** and I\'ll ship them via Mode C._',
].join("\n");

// No findings at all — build_audit_report()'s "clean" branch.
const REAL_AUDIT_FOOTER_CLEAN = [
  "**Audit report — acme/widgets**",
  "_5 file(s) deep-scanned · 0 critical · 0 high · 0 medium · 0 low_",
  "",
  "✅ No major issues found. Codebase looks clean.",
].join("\n");

describe("STEP 1(a) — detectShipCtaFallback must not fire on the real build_audit_report footer", () => {
  it("critical-label footer ('...I'll ship them via Mode C.') -> shouldFallback false", () => {
    expect(detectShipCtaFallback(REAL_AUDIT_FOOTER_CRITICAL)).toEqual({
      shouldFallback: false,
      hasFence: false,
    });
  });

  it("low-priority-label footer -> shouldFallback false", () => {
    expect(detectShipCtaFallback(REAL_AUDIT_FOOTER_LOW).shouldFallback).toBe(false);
  });

  it("clean/no-findings report (no footer at all) -> shouldFallback false", () => {
    expect(detectShipCtaFallback(REAL_AUDIT_FOOTER_CLEAN).shouldFallback).toBe(false);
  });
});

describe("STEP 1(b) — near-miss phrases that must NOT be treated as a ship/approve CTA", () => {
  it.each([
    ["ship them", "I found a few issues — I'll ship them once you confirm."],
    ["relationship it", "This fixes the relationship it has with the parent component."],
    ["ship these changes", "I can ship these changes whenever you're ready."],
    ["I'll shipping the", "Typo-laden model output: I'll shipping the fix now."],
  ])("%s -> shouldFallback false", (_label, text) => {
    expect(detectShipCtaFallback(text).shouldFallback).toBe(false);
  });
});

describe("STEP 1(c) — full MessageBubble render of an audit-report message shows no banner", () => {
  it("assistant audit-report message (mode-e-auditor provider, no fence) renders with NO ship-cta-fallback banner", () => {
    render(
      <MemoryRouter>
        <MessageBubble
          idx={0}
          m={{
            role: "assistant",
            content: REAL_AUDIT_FOOTER_CRITICAL,
            provider: "mode-e-auditor",
            mode: "E",
            streaming: false,
          }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("ship-cta-fallback-0")).toBeNull();
  });

  it("same audit-report text WITHOUT a provider marker still shows no banner (regex fix alone must also hold)", () => {
    render(
      <MemoryRouter>
        <MessageBubble
          idx={1}
          m={{
            role: "assistant",
            content: REAL_AUDIT_FOOTER_CRITICAL,
            provider: "mock",
            streaming: false,
          }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("ship-cta-fallback-1")).toBeNull();
  });
});

describe("STEP 3 — manual flow check: scan reply shows no banner, then a real fix handoff shows a real button", () => {
  it("the exact reported screenshot flow, as one sequential render: audit-scan turn (idx 0, no banner) followed by a 'fix' turn with a real handoff fence (idx 1, real button, no fallback)", () => {
    const scanReply = {
      role: "assistant",
      content: REAL_AUDIT_FOOTER_CRITICAL,
      provider: "mode-e-auditor",
      mode: "E",
      streaming: false,
    };
    const userFixPrompt = { role: "user", content: "fix the critical issues" };
    const realHandoffReply = {
      role: "assistant",
      content: [
        "Fixing the SQL injection now.",
        "",
        "```aurem-handoff",
        "Fix the SQL injection in app.py by using parameterized queries.",
        "```",
      ].join("\n"),
      provider: "mock",
      streaming: false,
    };

    render(
      <MemoryRouter>
        <div>
          <MessageBubble idx={0} m={scanReply} sessionId="sess-1" activeProject={ACTIVE_PROJECT} exhausted={false} />
          <MessageBubble idx={1} m={userFixPrompt} sessionId="sess-1" activeProject={ACTIVE_PROJECT} exhausted={false} />
          <MessageBubble idx={2} m={realHandoffReply} sessionId="sess-1" activeProject={ACTIVE_PROJECT} exhausted={false} />
        </div>
      </MemoryRouter>,
    );

    // Scan reply: no fallback banner, no button (it's a report, not a fix).
    expect(screen.queryByTestId("ship-cta-fallback-0")).toBeNull();
    expect(screen.queryByTestId("ship-cto-0-approve-btn")).toBeNull();
    // Real handoff turn: the Approve button renders, and the fallback
    // banner never also fires alongside it.
    expect(screen.getByTestId("ship-cto-2-approve-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("ship-cta-fallback-2")).toBeNull();
  });
});
