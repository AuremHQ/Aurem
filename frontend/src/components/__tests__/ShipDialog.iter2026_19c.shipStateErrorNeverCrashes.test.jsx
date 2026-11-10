/**
 * ShipDialog.iter2026_19c.shipStateErrorNeverCrashes.test.jsx
 *
 * 2026-10-27 — CRITICAL regression, live-reproduced by the testing
 * agent. `services/github_errors.py`'s structured GitHub-failure
 * detail ({code,message,retryable}) combined with MessageBubble.jsx's
 * OLD `doSubmitShipTarget` catch (`e?.response?.data?.detail ||
 * e?.message`) put that raw OBJECT into `shipState.error`.
 * `ShipDialog` passes `error={shipState.error}` straight into
 * `ShipApproveCard`, which used to render `{error}` directly as a
 * React child — "Objects are not valid as a React child" threw, and
 * the route error boundary replaced the ENTIRE chat page.
 *
 * This renders the REAL chain (ShipDialog -> ShipApproveCard ->
 * PrMiniGuide, nothing mocked except the two heavy live-polling
 * cards per the existing ShipDialog.iter2026_10c test's convention)
 * with `shipState.error` set to a structured detail object — proving
 * the render never throws even if a future caller regresses and
 * forgets to sanitize first.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ShipDialog from "../ShipDialog";

vi.mock("../TaskLiveTape", () => ({ default: () => <div data-testid="mock-tape" /> }));
vi.mock("../TaskProgressCard", () => ({ default: () => <div data-testid="mock-progress-card" /> }));

const baseProps = {
  idx: 0,
  msg: {},
  handoffBrief: { files: ["README.md"] },
  canShip: true,
  exhausted: false,
  activeProject: { project_id: "p1", github_owner: "acme", github_repo: "widgets", branch: "main" },
  onShip: vi.fn(),
  onRollback: vi.fn(),
  onOpenDeployTab: vi.fn(),
  onOpenLivePopup: vi.fn(),
};

describe("ShipDialog — shipState.error never crashes the page (iter 19c)", () => {
  it("a structured {code,message,retryable} object error renders safely, never throws", () => {
    const shipState = {
      status: "error",
      taskId: null,
      error: { code: "github_timeout", message: "Couldn't reach GitHub in time — please try again.", retryable: true },
    };
    expect(() => render(<ShipDialog {...baseProps} shipState={shipState} />)).not.toThrow();
    const status = screen.getByTestId("ship-cto-0-status");
    expect(status.textContent).not.toBe("[object Object]");
    expect(status.textContent.length).toBeGreaterThan(0);
  });

  it("the real sanitized string a fixed caller produces renders verbatim", () => {
    const shipState = { status: "error", taskId: null, error: "Couldn't reach GitHub in time — please try again." };
    render(<ShipDialog {...baseProps} shipState={shipState} />);
    expect(screen.getByTestId("ship-cto-0-status").textContent)
      .toBe("Couldn't reach GitHub in time — please try again.");
  });
});
