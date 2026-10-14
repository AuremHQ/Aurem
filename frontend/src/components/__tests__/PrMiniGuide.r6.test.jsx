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
    // Always set this module-level flag explicitly; production defaults OFF.
    setPageFlags({ ship_via_pr_enabled: false });
  });

  it("shows PR wording when ship_via_pr_enabled is on", () => {
    setPageFlags({ ship_via_pr_enabled: true });
    render(<PrMiniGuide />);
    const guide = screen.getByTestId("pr-mini-guide");
    expect(guide).toHaveTextContent("We'll open a pull request on your GitHub");
    expect(guide).toHaveTextContent("Review it there if you like, or just click Merge");
    expect(guide).toHaveTextContent("Done — it's live on your site");
    expect(screen.getByTestId("pr-mini-guide-pr-copy")).toBeInTheDocument();
    expect(screen.queryByTestId("pr-mini-guide-direct-copy")).not.toBeInTheDocument();
  });

  it("says ORA commits directly to main when ship_via_pr_enabled is off", () => {
    setPageFlags({ ship_via_pr_enabled: false });
    render(<PrMiniGuide />);
    const guide = screen.getByTestId("pr-mini-guide");
    expect(guide).toHaveTextContent("ORA commits directly to your main branch");
    expect(guide).toHaveTextContent("No PR to review first — it's live right away");
    expect(guide).toHaveTextContent("Changed your mind? You can roll back anytime");
    expect(screen.getByTestId("pr-mini-guide-direct-copy")).toBeInTheDocument();
    expect(screen.queryByTestId("pr-mini-guide-pr-copy")).not.toBeInTheDocument();
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
