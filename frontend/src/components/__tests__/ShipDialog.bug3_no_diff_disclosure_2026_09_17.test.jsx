/**
 * 2026-09-17 — Bug 3 fix (live QA report, "Approve the fix" ships
 * with no diff shown, "double-checked by a second reviewer" /
 * "plan clean ✓" with zero visible content).
 *
 * At the point ShipDialog renders its idle state, no code has been
 * generated yet — it's written AFTER the user clicks Approve, so
 * there is genuinely no diff to preview here (this is a workflow
 * property, not a bug to hide behind a disabled button). The fix is
 * honest disclosure: say plainly that there's nothing to review yet
 * and what happens next, and correct the Maxx-mode tooltip, which
 * factually claimed "a second AI double-checks this fix" while
 * services/agents.py::review_tail explicitly sets
 * `was_reviewed: False` for maxx mode (security scan only).
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ShipDialog from "../ShipDialog";

vi.mock("../TaskLiveTape", () => ({ default: () => <div data-testid="mock-tape" /> }));
vi.mock("../TaskProgressCard", () => ({ default: () => <div data-testid="mock-progress-card" /> }));
vi.mock("../ShipLintBadge", () => ({ default: () => <div data-testid="mock-lint-badge" /> }));

const baseProps = {
  idx: 0,
  msg: { maxxMode: true },
  handoffBrief: { files: ["README.md"] },
  canShip: true,
  exhausted: false,
  shipState: { status: "idle" },
  taskInfo: null,
  activeProject: { project_id: "p1", github_owner: "o", github_repo: "r", branch: "main" },
  onShip: vi.fn(),
  onRollback: vi.fn(),
  onOpenDeployTab: vi.fn(),
  onOpenLivePopup: vi.fn(),
};

describe("ShipDialog — honest pre-approval disclosure (Bug 3 fix)", () => {
  it("t_no_diff_yet_disclosure_shown_alongside_approve_button", () => {
    render(<ShipDialog {...baseProps} />);
    expect(screen.getByTestId("ship-no-diff-yet-0")).toBeInTheDocument();
    expect(screen.getByTestId("ship-no-diff-yet-0").textContent).toMatch(
      /no diff to review yet/i,
    );
  });

  it("t_maxx_chip_no_longer_claims_a_second_reviewer", () => {
    render(<ShipDialog {...baseProps} />);
    const chip = screen.getByTestId("ship-maxx-chip-0");
    expect(chip.getAttribute("title")).not.toMatch(/second AI double-checks/i);
    expect(chip.getAttribute("title")).toMatch(/no separate second-reviewer pass/i);
  });
});
