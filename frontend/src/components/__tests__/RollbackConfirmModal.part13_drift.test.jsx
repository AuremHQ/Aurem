/**
 * RollbackConfirmModal.part13_drift.test.jsx — Part XIII · 1.
 *
 * The modal's drift variant: when the backend answers rollback_status
 * "drift_detected", the caller reopens the modal with `drift` set and
 * the modal becomes the explicit "branch has moved — acknowledge
 * drift and roll back anyway?" step (confirm resends with
 * acknowledge_drift: true — the caller owns the resend).
 */
import React from "react";
import {
  render, screen, fireEvent, cleanup,
} from "@testing-library/react";
import {
  describe, it, expect, afterEach, vi,
} from "vitest";

import RollbackConfirmModal from "../RollbackConfirmModal";

describe("Part XIII · 1 — RollbackConfirmModal drift variant", () => {
  afterEach(() => cleanup());

  const drift = {
    branch: "main",
    expected: "aaa111aaa111",
    current: "bbb222bbb222",
    detail: "Branch has changed since the fix was applied. Current: bbb222bbb222. Expected: aaa111aaa111.",
  };

  it("plain confirm (no drift) is unchanged: 'Rollback' button, no drift detail", () => {
    render(
      <RollbackConfirmModal
        open
        shortLabel="shipped commit abc1234"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByTestId("rollback-confirm-modal")).toBeInTheDocument();
    expect(screen.getByTestId("rollback-confirm-approve").textContent)
      .toMatch(/rollback/i);
    expect(screen.queryByTestId("rollback-confirm-drift-detail"))
      .not.toBeInTheDocument();
  });

  it("drift variant shows the 'branch has moved' step with branch/expected/current", () => {
    render(
      <RollbackConfirmModal
        open
        shortLabel="shipped commit abc1234"
        drift={drift}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const detail = screen.getByTestId("rollback-confirm-drift-detail");
    expect(detail.textContent).toMatch(/branch has moved/i);
    expect(detail.textContent).toMatch(/acknowledge drift/i);
    expect(detail.textContent).toContain("main");
    expect(detail.textContent).toContain("aaa111aaa111");
    expect(detail.textContent).toContain("bbb222bbb222");
  });

  it("drift variant confirm button reads 'Roll back anyway' and fires onConfirm", () => {
    const onConfirm = vi.fn();
    render(
      <RollbackConfirmModal
        open
        shortLabel="shipped commit abc1234"
        drift={drift}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    const btn = screen.getByTestId("rollback-confirm-approve");
    expect(btn.textContent).toMatch(/roll back anyway/i);
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("cancel still fires onCancel in the drift variant", () => {
    const onCancel = vi.fn();
    render(
      <RollbackConfirmModal
        open
        shortLabel="shipped commit abc1234"
        drift={drift}
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByTestId("rollback-confirm-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("closed modal renders nothing (drift or not)", () => {
    const { container } = render(
      <RollbackConfirmModal
        open={false}
        shortLabel="shipped commit abc1234"
        drift={drift}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
