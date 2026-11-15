/**
 * ShipApproveCard.r3.test.jsx — R3 (this round), the ONE shared
 * "Approve the fix" card used by all 3 ship entry points.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ShipApproveCard from "../ShipApproveCard.jsx";

describe("ShipApproveCard (R3 — physical ship-UI unification)", () => {
  beforeEach(() => { localStorage.clear(); });

  it("renders Approve + Cancel when pending, and the PR mini-guide on first render", () => {
    const onApprove = vi.fn();
    render(<ShipApproveCard status="idle" onApprove={onApprove} onCancel={vi.fn()} />);
    expect(screen.getByTestId("ship-approve-approve-btn")).toBeInTheDocument();
    expect(screen.getByTestId("pr-mini-guide")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("ship-approve-approve-btn"));
    expect(onApprove).toHaveBeenCalled();
  });

  it("shows a busy spinner while shipping (no approve button)", () => {
    render(<ShipApproveCard status="shipping" />);
    expect(screen.queryByTestId("ship-approve-approve-btn")).not.toBeInTheDocument();
    expect(screen.getByTestId("ship-approve-status").textContent).toContain("Shipping");
  });

  it("shows 'Shipped' + a Rollback button when live", () => {
    const onRollback = vi.fn();
    render(<ShipApproveCard status="shipped" onRollback={onRollback} />);
    expect(screen.getByTestId("ship-approve-status").textContent).toBe("Shipped");
    fireEvent.click(screen.getByTestId("ship-approve-rollback-btn"));
    expect(onRollback).toHaveBeenCalled();
  });

  it("shows 'Rolled back' (never 'Reverted') for any surface's rollback-done status", () => {
    render(<ShipApproveCard status="reverted" />);
    expect(screen.getByTestId("ship-approve-status").textContent).toBe("Rolled back");
  });

  it("shows a Retry button on failure", () => {
    const onRetry = vi.fn();
    render(<ShipApproveCard status="error" error="Ship failed" onRetry={onRetry} />);
    fireEvent.click(screen.getByTestId("ship-approve-retry-btn"));
    expect(onRetry).toHaveBeenCalled();
  });

  it("renders the same testids regardless of which entry point passes it (contract check)", () => {
    const { unmount } = render(<ShipApproveCard testidPrefix="ship-approve" status="idle" onApprove={vi.fn()} />);
    expect(screen.getByTestId("ship-approve")).toBeInTheDocument();
    unmount();
    render(<ShipApproveCard testidPrefix="ship-approve" status="confirm" onApprove={vi.fn()} />);
    expect(screen.getByTestId("ship-approve-approve-btn")).toBeInTheDocument();
  });

  // 2026-10-27 — CRITICAL regression, live-reproduced by the testing
  // agent. A structured GitHub-failure detail object
  // ({code,message,retryable}) reaching `error` here used to throw
  // "Objects are not valid as a React child" and crash the ENTIRE
  // chat page (caught by the route error boundary). Callers are now
  // fixed to always sanitize to a string first (see
  // MessageBubble.jsx's doSubmitShipTarget + lib/cleanErr.js's
  // sanitizeApiErrorDetail), but this is the defense-in-depth half of
  // that fix: ShipApproveCard itself must never crash even if a
  // FUTURE caller makes the same mistake again.
  it("never crashes on a non-string error (object) — falls back to the status label instead", () => {
    const badDetail = { code: "github_timeout", message: "Couldn't reach GitHub in time.", retryable: true };
    expect(() => render(<ShipApproveCard status="error" error={badDetail} onRetry={vi.fn()} />))
      .not.toThrow();
    const status = screen.getByTestId("ship-approve-status");
    expect(status.textContent).not.toBe("[object Object]");
    expect(status.textContent.length).toBeGreaterThan(0);
  });

  it("a real string error still renders verbatim (no regression from the typeof guard)", () => {
    render(<ShipApproveCard status="error" error="Couldn't reach GitHub in time." onRetry={vi.fn()} />);
    expect(screen.getByTestId("ship-approve-status").textContent).toBe("Couldn't reach GitHub in time.");
  });
});
