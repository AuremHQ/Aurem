/**
 * ShipDialog.iter2026_10c.approveFixBranchPrCopy.test.jsx
 *
 * Phase 1 fix — founder-reported 2026-10-21 incident: "Approve the
 * fix" (this surface — Mode D chat -> ShipDialog -> shipViaCTO() ->
 * worker_git.py) pushed straight to the project's base branch while
 * the pre-approve PrMiniGuide copy said "We'll open a pull request on
 * your GitHub" — a promise that depended on the `ship_via_pr` flag,
 * which worker_git.py never actually read. worker_git.py now ALWAYS
 * branches + best-effort-PRs regardless of that flag, so this
 * surface's copy must say so unconditionally too — see
 * ShipDialog.jsx's `prMiniGuideMode="branch_and_pr"` and
 * PrMiniGuide.jsx's own 2026-10 comment.
 *
 * This test does NOT mock ShipApproveCard/PrMiniGuide — it renders
 * the real chain (ShipDialog -> ShipApproveCard -> PrMiniGuide) to
 * prove the prop actually reaches the leaf component, with the
 * `ship_via_pr` flag flipped both ways.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import ShipDialog from "../ShipDialog";
import { setPageFlags } from "../../lib/pageFlags";

vi.mock("../TaskLiveTape", () => ({ default: () => <div data-testid="mock-tape" /> }));
vi.mock("../TaskProgressCard", () => ({ default: () => <div data-testid="mock-progress-card" /> }));

const baseProps = {
  idx: 0,
  msg: {},
  handoffBrief: { files: ["README.md"] },
  canShip: true,
  exhausted: false,
  shipState: { status: "idle" },
  activeProject: { project_id: "p1", github_owner: "acme", github_repo: "widgets", branch: "main" },
  onShip: vi.fn(),
  onRollback: vi.fn(),
  onOpenDeployTab: vi.fn(),
  onOpenLivePopup: vi.fn(),
};

describe("ShipDialog 'Approve the fix' — PrMiniGuide copy is flag-independent", () => {
  beforeEach(() => {
    try { localStorage.removeItem("aurem_pr_mini_guide_seen_v1"); } catch { /* ignore */ }
  });

  it("ship_via_pr flag OFF: still shows the branch+PR copy, never the direct-to-main copy", () => {
    setPageFlags({ ship_via_pr_enabled: false });
    render(<ShipDialog {...baseProps} />);
    expect(screen.getByTestId("pr-mini-guide-branch-pr-copy")).toBeInTheDocument();
    expect(screen.queryByTestId("pr-mini-guide-direct-copy")).not.toBeInTheDocument();
    expect(screen.getByText(/never straight to main/i)).toBeInTheDocument();
  });

  it("ship_via_pr flag ON: shows the SAME branch+PR copy, never the generic 'we'll open a pull request' promise", () => {
    setPageFlags({ ship_via_pr_enabled: true });
    render(<ShipDialog {...baseProps} />);
    expect(screen.getByTestId("pr-mini-guide-branch-pr-copy")).toBeInTheDocument();
    expect(screen.queryByTestId("pr-mini-guide-pr-copy")).not.toBeInTheDocument();
    expect(screen.getByText(/never straight to main/i)).toBeInTheDocument();
  });

  it("approve button's title no longer implies a direct push to the base branch", () => {
    setPageFlags({ ship_via_pr_enabled: false });
    render(<ShipDialog {...baseProps} />);
    const btn = screen.getByTestId("ship-cto-0-approve-btn");
    expect(btn.title).toMatch(/new branch/i);
    expect(btn.title).not.toBe("Ship to acme/widgets@main");
  });
});
