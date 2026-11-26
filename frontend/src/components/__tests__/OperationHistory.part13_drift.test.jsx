/**
 * OperationHistory.part13_drift.test.jsx — Part XIII · 1.
 *
 * Drift-acknowledge rollback in the history surface:
 *   • A drift-blocked rollback row renders its own label (not
 *     "Rolling back…") and the ship row KEEPS its Rollback button
 *     (the backend attempted nothing — it is retryable).
 *   • performRowRollback inspects the rollbackLoop response: on
 *     rollback_status "drift_detected" it does NOT open the progress
 *     stream — the modal reopens in its drift variant, and confirming
 *     there resends with acknowledge_drift: true. The confirmed
 *     retry then opens the progress stream as normal.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";

const rollbackLoopMock = vi.fn();
vi.mock("../../lib/loopApi", () => ({
  streamLoopEvents: vi.fn(),
  rollbackLoop: (...a) => rollbackLoopMock(...a),
}));

import OperationHistory from "../OperationHistory.jsx";
import * as loopApi from "../../lib/loopApi";

let subs;

function seedHistory(items) {
  global.fetch = vi.fn(() => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ items }),
  }));
}

const DRIFT_RESPONSE = {
  ok: false,
  loop_id: "loopdrift01",
  rollback_status: "drift_detected",
  commit_sha: "deadbeefcafe",
  detail: "Branch has changed since the fix was applied. Current: bbb222. Expected: aaa111.",
  drift: { branch: "main", expected: "aaa111aaa111", current: "bbb222bbb222" },
};

beforeEach(() => {
  subs = [];
  rollbackLoopMock.mockReset();
  loopApi.streamLoopEvents.mockImplementation((loopId, cb) => {
    const sub = { loopId, cb, abort: vi.fn() };
    subs.push(sub);
    return { abort: sub.abort };
  });
});
afterEach(() => { vi.clearAllMocks(); });

describe("Part XIII · 1 — drift-blocked rows stay retryable", () => {
  it("drift_blocked rollback row shows the blocked label and the ship row keeps its Rollback button", async () => {
    seedHistory([
      { loop_id: "loopdrift01", op_type: "ship", state: "completed",
        all_passed: true, step_count: 5, commit_sha: "deadbeef",
        started_at: "2026-07-27T10:00:00Z", finished_at: "2026-07-27T10:02:00Z" },
      { loop_id: "loopdrift01", op_type: "rollback", state: "drift_blocked",
        all_passed: false, step_count: 0,
        started_at: "2026-07-27T11:00:00Z", finished_at: "2026-07-27T11:01:00Z" },
    ]);
    render(<OperationHistory projectId="p1" activeLoopId={null} />);
    await waitFor(() => {
      expect(screen.getByText(/Rollback blocked — branch moved/)).toBeInTheDocument();
    });
    // The ship row's button is still there (drift attempted nothing).
    expect(screen.getByTestId("op-history-rollback-btn-loopdrif")).toBeInTheDocument();
  });
});

describe("Part XIII · 1 — drift confirm flow in OperationHistory", () => {
  it("drift response reopens the modal (no stream) and the confirmed retry sends acknowledge_drift", async () => {
    seedHistory([
      { loop_id: "loopdrift01", op_type: "ship", state: "completed",
        all_passed: true, step_count: 5, commit_sha: "deadbeef",
        started_at: "2026-07-27T10:00:00Z", finished_at: "2026-07-27T10:02:00Z" },
    ]);
    rollbackLoopMock
      .mockResolvedValueOnce(DRIFT_RESPONSE)
      .mockResolvedValueOnce({ ok: true, loop_id: "loopdrift01", rollback_status: "queued" });
    render(<OperationHistory projectId="p1" activeLoopId={null} />);
    await waitFor(() => {
      expect(screen.getByTestId("op-history-rollback-btn-loopdrif")).toBeInTheDocument();
    });

    // First confirm (plain modal) → drift response.
    fireEvent.click(screen.getByTestId("op-history-rollback-btn-loopdrif"));
    expect(screen.getByTestId("rollback-confirm-modal")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByTestId("rollback-confirm-approve"));
    });
    await waitFor(() => {
      expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    });
    expect(rollbackLoopMock).toHaveBeenCalledWith(
      "loopdrift01",
    );
    // NO progress stream opened on the drift response.
    expect(loopApi.streamLoopEvents).not.toHaveBeenCalled();

    // Modal reopened in its drift variant.
    await waitFor(() => {
      expect(screen.getByTestId("rollback-confirm-drift-detail")).toBeInTheDocument();
    });
    expect(screen.getByTestId("rollback-confirm-drift-detail").textContent)
      .toMatch(/branch has moved/i);
    expect(screen.getByTestId("rollback-confirm-approve").textContent)
      .toMatch(/roll back anyway/i);

    // Confirmed retry → acknowledge_drift: true → queued → stream opens.
    await act(async () => {
      fireEvent.click(screen.getByTestId("rollback-confirm-approve"));
    });
    await waitFor(() => {
      expect(rollbackLoopMock).toHaveBeenCalledTimes(2);
    });
    expect(rollbackLoopMock).toHaveBeenLastCalledWith(
      "loopdrift01",
      { acknowledgeDrift: true },
    );
    await waitFor(() => {
      expect(loopApi.streamLoopEvents).toHaveBeenCalledWith(
        "loopdrift01",
        expect.any(Object),
      );
    });
  });

  it("a queued (non-drift) rollback opens the progress stream immediately", async () => {
    seedHistory([
      { loop_id: "loopok0002", op_type: "ship", state: "completed",
        all_passed: true, step_count: 5, commit_sha: "deadbeef",
        started_at: "2026-07-27T10:00:00Z", finished_at: "2026-07-27T10:02:00Z" },
    ]);
    rollbackLoopMock.mockResolvedValueOnce(
      { ok: true, loop_id: "loopok0002", rollback_status: "queued" },
    );
    render(<OperationHistory projectId="p1" activeLoopId={null} />);
    await waitFor(() => {
      expect(screen.getByTestId("op-history-rollback-btn-loopok00")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("op-history-rollback-btn-loopok00"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("rollback-confirm-approve"));
    });
    await waitFor(() => {
      expect(loopApi.streamLoopEvents).toHaveBeenCalledWith(
        "loopok0002",
        expect.any(Object),
      );
    });
    expect(screen.queryByTestId("rollback-confirm-drift-detail"))
      .not.toBeInTheDocument();
  });
});
