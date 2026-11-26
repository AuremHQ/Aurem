/**
 * LoopLiveFeed.part13_drift.test.jsx — Part XIII · 1.
 *
 * Drift-acknowledge rollback in the ShippedRow:
 *   • A drift response (HTTP 200, ok: false, rollback_status
 *     "drift_detected") does NOT hand off to OperationHistory and
 *     does NOT call onRollbackStarted — the modal reopens in its
 *     drift variant instead.
 *   • Confirming "Roll back anyway" resends with
 *     acknowledge_drift: true; a queued response then hands off.
 */
import React from "react";
import {
  render, fireEvent, cleanup, act, waitFor,
} from "@testing-library/react";
import {
  describe, it, expect, vi, beforeEach, afterEach,
} from "vitest";

const rollbackLoopMock = vi.fn();
vi.mock("../../lib/loopApi", () => ({
  rollbackLoop: (...a) => rollbackLoopMock(...a),
  streamLoopEvents: vi.fn(() => ({ abort: () => {} })),
  getLoopStatus: vi.fn(() => Promise.resolve({})),
}));

import {
  ShippedRow, _resetRollbackRegistriesForTests,
} from "../LoopLiveFeed";

const ship = {
  shortSha: "abc1234",
  fullSha: "abc1234".padEnd(40, "d"),
  htmlUrl: "https://github.com/x/y/commit/abc1234",
};

const DRIFT_RESPONSE = {
  ok: false,
  loop_id: "lp-d1",
  rollback_status: "drift_detected",
  commit_sha: "deadbeefcafe",
  detail: "Branch has changed since the fix was applied. Current: bbb222. Expected: aaa111.",
  drift: { branch: "main", expected: "aaa111aaa111", current: "bbb222bbb222" },
};

const btn = () =>
  document.querySelector('[data-testid="loop-shipped-rollback-btn-abc1234"]');
const approveBtn = () =>
  document.querySelector('[data-testid="rollback-confirm-approve"]');

describe("Part XIII · 1 — ShippedRow drift-acknowledge rollback", () => {
  beforeEach(() => {
    rollbackLoopMock.mockReset();
    _resetRollbackRegistriesForTests();
  });
  afterEach(() => { cleanup(); });

  it("drift response reopens the modal in its drift variant (no hand-off)", async () => {
    rollbackLoopMock.mockResolvedValueOnce(DRIFT_RESPONSE);
    const onRollbackStarted = vi.fn();
    render(<ShippedRow loopId="lp-d1" ship={ship} onRollbackStarted={onRollbackStarted} />);
    await act(async () => {});
    fireEvent.pointerDown(btn());
    expect(document.querySelector('[data-testid="rollback-confirm-modal"]'))
      .toBeInTheDocument();
    await act(async () => { fireEvent.click(approveBtn()); });
    await waitFor(() => expect(rollbackLoopMock).toHaveBeenCalledTimes(1));
    expect(rollbackLoopMock).toHaveBeenCalledWith("lp-d1");
    // No hand-off on drift: parent never told, phase never handed-off.
    expect(onRollbackStarted).not.toHaveBeenCalled();
    // Modal reopened in the drift variant.
    await waitFor(() => {
      expect(document.querySelector('[data-testid="rollback-confirm-drift-detail"]'))
        .toBeInTheDocument();
    });
    expect(document.querySelector('[data-testid="rollback-confirm-drift-detail"]').textContent)
      .toMatch(/branch has moved/i);
    expect(approveBtn().textContent).toMatch(/roll back anyway/i);
  });

  it("confirming 'Roll back anyway' resends with acknowledge_drift and then hands off", async () => {
    rollbackLoopMock
      .mockResolvedValueOnce(DRIFT_RESPONSE)
      .mockResolvedValueOnce({ ok: true, loop_id: "lp-d1", rollback_status: "queued" });
    const onRollbackStarted = vi.fn();
    render(<ShippedRow loopId="lp-d1" ship={ship} onRollbackStarted={onRollbackStarted} />);
    await act(async () => {});
    fireEvent.pointerDown(btn());
    await act(async () => { fireEvent.click(approveBtn()); });
    await waitFor(() => {
      expect(document.querySelector('[data-testid="rollback-confirm-drift-detail"]'))
        .toBeInTheDocument();
    });
    await act(async () => { fireEvent.click(approveBtn()); });
    await waitFor(() => expect(rollbackLoopMock).toHaveBeenCalledTimes(2));
    expect(rollbackLoopMock).toHaveBeenLastCalledWith("lp-d1", { acknowledgeDrift: true });
    await waitFor(() => expect(onRollbackStarted).toHaveBeenCalledWith("lp-d1"));
  });

  it("a queued (non-drift) rollback hands off immediately, no drift UI", async () => {
    rollbackLoopMock.mockResolvedValueOnce(
      { ok: true, loop_id: "lp-d1", rollback_status: "queued" },
    );
    const onRollbackStarted = vi.fn();
    render(<ShippedRow loopId="lp-d1" ship={ship} onRollbackStarted={onRollbackStarted} />);
    await act(async () => {});
    fireEvent.pointerDown(btn());
    await act(async () => { fireEvent.click(approveBtn()); });
    await waitFor(() => expect(onRollbackStarted).toHaveBeenCalledWith("lp-d1"));
    expect(document.querySelector('[data-testid="rollback-confirm-drift-detail"]'))
      .toBeNull();
  });
});
