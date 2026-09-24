/**
 * useShipState.r3.test.jsx — R3 (this round), shared ship state hook.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { renderHook, act } from "@testing-library/react";
import { useShipState, toCanonicalShipStatus, useRollbackConfirm } from "../../lib/useShipState";
import { SHIP_STATUS } from "../../lib/shipStatus";

describe("toCanonicalShipStatus — maps every surface's own vocabulary (R3)", () => {
  it("maps ShipDialog/TaskProgressCard's raw statuses", () => {
    expect(toCanonicalShipStatus("idle")).toBe(SHIP_STATUS.PENDING);
    expect(toCanonicalShipStatus("shipping")).toBe(SHIP_STATUS.SHIPPING);
    expect(toCanonicalShipStatus("shipped")).toBe(SHIP_STATUS.LIVE);
    expect(toCanonicalShipStatus("reverted")).toBe(SHIP_STATUS.ROLLED_BACK);
    expect(toCanonicalShipStatus("error")).toBe(SHIP_STATUS.FAILED);
  });

  it("maps ShipConfirmModal's raw statuses", () => {
    expect(toCanonicalShipStatus("confirm")).toBe(SHIP_STATUS.PENDING);
    expect(toCanonicalShipStatus("shipped")).toBe(SHIP_STATUS.LIVE);
  });

  it("maps LoopLiveFeed's rollback sub-phases", () => {
    expect(toCanonicalShipStatus("submitting")).toBe(SHIP_STATUS.ROLLING_BACK);
    expect(toCanonicalShipStatus("handed-off")).toBe(SHIP_STATUS.ROLLING_BACK);
    expect(toCanonicalShipStatus("completed")).toBe(SHIP_STATUS.ROLLED_BACK);
  });
});

describe("useShipState — derived flags (R3)", () => {
  it("computes canApprove/canRollback/canRetry correctly for each canonical phase", () => {
    const { result: pending } = renderHook(() => useShipState("idle"));
    expect(pending.current.canApprove).toBe(true);
    expect(pending.current.canRollback).toBe(false);

    const { result: live } = renderHook(() => useShipState("shipped"));
    expect(live.current.isLive).toBe(true);
    expect(live.current.canRollback).toBe(true);
    expect(live.current.canApprove).toBe(false);

    const { result: failed } = renderHook(() => useShipState("error"));
    expect(failed.current.isFailed).toBe(true);
    expect(failed.current.canRetry).toBe(true);
  });
});

describe("useRollbackConfirm — the ONE shared confirm->execute path (R3)", () => {
  it("opens on request, calls rollbackFn on confirm, closes after", async () => {
    const rollbackFn = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useRollbackConfirm(rollbackFn));
    expect(result.current.open).toBe(false);

    act(() => result.current.request());
    expect(result.current.open).toBe(true);

    await act(async () => { await result.current.confirm("loop-1"); });
    expect(rollbackFn).toHaveBeenCalledWith("loop-1");
    expect(result.current.open).toBe(false);
    expect(result.current.busy).toBe(false);
  });

  it("cancel closes without calling rollbackFn", () => {
    const rollbackFn = vi.fn();
    const { result } = renderHook(() => useRollbackConfirm(rollbackFn));
    act(() => result.current.request());
    act(() => result.current.cancel());
    expect(result.current.open).toBe(false);
    expect(rollbackFn).not.toHaveBeenCalled();
  });
});
