/**
 * PrMiniGuide.r6_test.jsx — R6 (overnight round).
 *
 * Plain-English 3-step "what happens next" guide shown on first ship
 * approval, dismissible, never shown again once dismissed. Also
 * proves it's wired into both ShipDialog (idle/pre-approve) and
 * ShipConfirmModal (confirm phase) — the two ship-approval entry
 * points this round explicitly named.
 */
import React from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import PrMiniGuide from "../PrMiniGuide.jsx";
import { setPageFlags } from "../../lib/pageFlags";

describe("PrMiniGuide (R6)", () => {
  beforeEach(() => {
    localStorage.clear();
    // CI-REPAIR (product changed, not a regression) — 2026-10 C4 fix in
    // PrMiniGuide.jsx: the guide now reads the REAL `ship_via_pr_enabled`
    // flag instead of hard-coding the PR story (verified against source —
    // production runs with this flag off, so the old hard-coded PR copy
    // was itself the bug this fix closed). Reset to the documented
    // default (false) before each test; the two branches below set it
    // explicitly rather than relying on module-load order.
    setPageFlags({ ship_via_pr_enabled: false });
  });

  it("shows the direct-commit copy when ship_via_pr is OFF (current production default)", () => {
    setPageFlags({ ship_via_pr_enabled: false });
    render(<PrMiniGuide />);
    const guide = screen.getByTestId("pr-mini-guide");
    expect(guide.textContent).toContain("ORA commits directly to your main branch");
    expect(guide.textContent).toContain("No PR to review first");
    expect(guide.textContent).toContain("You can roll back anytime");
  });

  it("shows the 3-step PR guide when ship_via_pr is ON", () => {
    setPageFlags({ ship_via_pr_enabled: true });
    render(<PrMiniGuide />);
    const guide = screen.getByTestId("pr-mini-guide");
    expect(guide.textContent).toContain("We'll open a pull request on your GitHub");
    expect(guide.textContent).toContain("Review it there if you like, or just click Merge");
    expect(guide.textContent).toContain("Done — it's live on your site");
  });

  it("dismisses and never shows again (localStorage-backed)", () => {
    render(<PrMiniGuide />);
    fireEvent.click(screen.getByTestId("pr-mini-guide-dismiss"));
    expect(screen.queryByTestId("pr-mini-guide")).not.toBeInTheDocument();
    expect(localStorage.getItem("aurem_pr_mini_guide_seen_v1")).toBe("1");
  });

  it("stays hidden on a fresh mount after being dismissed once", () => {
    localStorage.setItem("aurem_pr_mini_guide_seen_v1", "1");
    render(<PrMiniGuide />);
    expect(screen.queryByTestId("pr-mini-guide")).not.toBeInTheDocument();
  });
});
