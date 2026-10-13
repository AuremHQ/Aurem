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
import { describe, it, expect, beforeEach, vi } from "vitest";
import { isShipViaPrEnabled } from "../../lib/pageFlags";

vi.mock("../../lib/pageFlags", () => ({
  isShipViaPrEnabled: vi.fn(() => false),
}));
import { render, screen, fireEvent } from "@testing-library/react";
import PrMiniGuide from "../PrMiniGuide.jsx";

describe("PrMiniGuide (R6)", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(isShipViaPrEnabled).mockReturnValue(false);
  });

  it("shows pull-request guidance when PR shipping is enabled", () => {
    vi.mocked(isShipViaPrEnabled).mockReturnValue(true);
    render(<PrMiniGuide />);
    const guide = screen.getByTestId("pr-mini-guide");
    expect(guide.textContent).toContain("We'll open a pull request on your GitHub");
    expect(guide.textContent).toContain("Review it there if you like, or just click Merge");
    expect(guide.textContent).toContain("Done — it's live on your site");
  });

  it("shows direct-commit guidance when PR shipping is disabled", () => {
    vi.mocked(isShipViaPrEnabled).mockReturnValue(false);
    render(<PrMiniGuide />);
    expect(screen.getByTestId("pr-mini-guide-direct-copy").textContent)
      .toContain("ORA commits directly to your main branch.");
    expect(screen.getByTestId("pr-mini-guide-direct-copy").textContent)
      .toContain("No PR to review first");
    expect(screen.getByTestId("pr-mini-guide-direct-copy").textContent)
      .toContain("Changed your mind? You can roll back anytime.");
  });

  it("shows direct-commit guidance when PR shipping is disabled", () => {
    vi.mocked(isShipViaPrEnabled).mockReturnValue(false);
    render(<PrMiniGuide />);
    expect(screen.getByTestId("pr-mini-guide-direct-copy").textContent)
      .toContain("ORA commits directly to your main branch.");
    expect(screen.getByTestId("pr-mini-guide-direct-copy").textContent)
      .toContain("No PR to review first");
    expect(screen.getByTestId("pr-mini-guide-direct-copy").textContent)
      .toContain("Changed your mind? You can roll back anytime.");
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
