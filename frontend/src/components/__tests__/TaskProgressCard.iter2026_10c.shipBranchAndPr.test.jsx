/**
 * TaskProgressCard.iter2026_10c.shipBranchAndPr.test.jsx
 *
 * Phase 1 fix — the success card must show the REAL branch the
 * commit landed on (worker_git.py now always pushes to a dedicated
 * branch, never the base branch) and the REAL PR status — never a
 * fabricated "shipped to main" or a fake PR link when none opened.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import TaskProgressCard from "../TaskProgressCard";

const PROJECT = { github_owner: "acme", github_repo: "widgets", branch: "main" };

describe("TaskProgressCard — honest branch + PR status (Phase 1 fix)", () => {
  it("shows the real ship branch and a working PR link when a PR was opened", () => {
    const task = {
      status: "done",
      commit_sha: "abc1234",
      ship_branch: "auremcto/ship-t123",
      base_branch: "main",
      pr_opened: true,
      pr_url: "https://github.com/acme/widgets/pull/42",
    };
    render(<TaskProgressCard taskId="t123" task={task} project={PROJECT} onRollback={vi.fn()} />);
    expect(screen.getByTestId("ship-branch-t123").textContent).toContain("auremcto/ship-t123");
    const prLink = screen.getByTestId("ship-pr-link-t123");
    expect(prLink).toHaveAttribute("href", "https://github.com/acme/widgets/pull/42");
    expect(screen.queryByTestId("ship-no-pr-t123")).not.toBeInTheDocument();
  });

  it("shows an honest 'no PR opened' note instead of a fake PR link when PR-open failed", () => {
    const task = {
      status: "done",
      commit_sha: "abc1234",
      ship_branch: "auremcto/ship-t124",
      base_branch: "main",
      pr_opened: false,
      pr_url: null,
    };
    render(<TaskProgressCard taskId="t124" task={task} project={PROJECT} onRollback={vi.fn()} />);
    expect(screen.getByTestId("ship-branch-t124").textContent).toContain("auremcto/ship-t124");
    expect(screen.getByTestId("ship-no-pr-t124").textContent.toLowerCase()).toContain("no pr opened");
    expect(screen.queryByTestId("ship-pr-link-t124")).not.toBeInTheDocument();
  });

  it("never claims a branch shipped when the worker never reported one (e.g. pre-fix task docs)", () => {
    const task = { status: "done", commit_sha: "abc1234" };
    render(<TaskProgressCard taskId="t125" task={task} project={PROJECT} onRollback={vi.fn()} />);
    expect(screen.queryByTestId("ship-branch-t125")).not.toBeInTheDocument();
  });
});
