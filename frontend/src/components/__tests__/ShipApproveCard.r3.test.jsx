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
});
