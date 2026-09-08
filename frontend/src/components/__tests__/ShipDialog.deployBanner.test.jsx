/**
 * 2026-09-11 — real QA repro (founder senior-QA pass): after a
 * successful rollback, the "🚀 Code shipped — ready to go live?"
 * banner kept showing even though the shipped commit had just been
 * reverted. Root cause: showDeployBanner only checked
 * `taskInfo.status === "done"` (the SHIP task's own lifecycle,
 * which never changes once the task finishes) and never looked at
 * `taskInfo.rollback_status` (set to "done" by
 * services/loop_rollback.py once a rollback actually completes).
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ShipDialog from "../ShipDialog";

vi.mock("../TaskLiveTape", () => ({ default: () => <div data-testid="mock-tape" /> }));
vi.mock("../TaskProgressCard", () => ({ default: () => <div data-testid="mock-progress-card" /> }));
vi.mock("../ShipApproveCard", () => ({ default: () => <div data-testid="mock-approve-card" /> }));
vi.mock("../ShipLintBadge", () => ({ default: () => <div data-testid="mock-lint-badge" /> }));

const baseProps = {
  idx: 0,
  msg: {},
  handoffBrief: { files: ["README.md"] },
  canShip: true,
  exhausted: false,
  shipState: { status: "shipped", taskId: "t1" },
  activeProject: { project_id: "p1", github_owner: "o", github_repo: "r", branch: "main" },
  onShip: vi.fn(),
  onRollback: vi.fn(),
  onOpenDeployTab: vi.fn(),
  onOpenLivePopup: vi.fn(),
};

describe("ShipDialog deploy banner (post-rollback staleness fix)", () => {
  it("t_deploy_banner_shows_when_task_done_and_not_rolled_back", () => {
    render(<ShipDialog {...baseProps} taskInfo={{ status: "done" }} />);
    expect(screen.getByTestId("ship-deploy-banner-0")).toBeInTheDocument();
  });

  it("t_deploy_banner_hidden_after_successful_rollback", () => {
    render(<ShipDialog {...baseProps} taskInfo={{ status: "done", rollback_status: "done" }} />);
    expect(screen.queryByTestId("ship-deploy-banner-0")).not.toBeInTheDocument();
  });

  it("t_deploy_banner_still_shows_if_rollback_only_failed", () => {
    // A FAILED rollback attempt must not hide the banner — the code
    // is still shipped/live in that case, nothing was reverted.
    render(<ShipDialog {...baseProps} taskInfo={{ status: "done", rollback_status: "failed" }} />);
    expect(screen.getByTestId("ship-deploy-banner-0")).toBeInTheDocument();
  });

  it("t_deploy_banner_hidden_while_task_still_running", () => {
    render(<ShipDialog {...baseProps} taskInfo={{ status: "running" }} />);
    expect(screen.queryByTestId("ship-deploy-banner-0")).not.toBeInTheDocument();
  });
});
