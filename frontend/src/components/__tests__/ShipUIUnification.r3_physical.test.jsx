/**
 * ShipUIUnification.r3_physical.test.jsx — R3 (2026-09-03) + P2-D
 * (2026-09-03, same day, founder's user-first wave — explicitly asked
 * to finish the merge R3 had deferred).
 *
 * Proves the PHYSICAL merge (not just shared labels): ALL 3 ship
 * entry points — ShipDialog (chat-inline), ShipConfirmModal (modal),
 * and LoopLiveFeed's ShippedRow (loop-mode) — import and render
 * <ShipApproveCard/>, itself built on useShipState(). ShippedRow's
 * click-safety (Iter 342 pointerdown guard) is preserved via a
 * dedicated prop, not silently dropped.
 */
import { readFileSync } from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const read = (p) => readFileSync(path.resolve(__dirname, p), "utf-8");

describe("R3 — ShipDialog delegates to the shared ShipApproveCard/useShipState", () => {
  const src = read("../ShipDialog.jsx");
  it("imports ShipApproveCard (not a bespoke button)", () => {
    expect(src).toContain('import ShipApproveCard from "./ShipApproveCard"');
  });
  it("renders it for the approve/retry row", () => {
    expect(src).toContain("<ShipApproveCard");
    expect(src).toContain("onApprove={onShip}");
  });
});

describe("R3 — ShipConfirmModal delegates to the shared ShipApproveCard/useShipState", () => {
  const src = read("../ShipConfirmModal.jsx");
  it("imports ShipApproveCard (not bespoke confirm/rollback/retry buttons)", () => {
    expect(src).toContain('import ShipApproveCard from "./ShipApproveCard"');
  });
  it("renders it for confirm/shipped/error/reverted, driven by the real phase state", () => {
    expect(src).toContain("<ShipApproveCard");
    expect(src).toContain('status={phase}');
    expect(src).toContain('onApprove={handleShip}');
    expect(src).toContain('onRollback={phase === "shipped" ? handleRollback : null}');
    expect(src).toContain('onRetry={phase === "error" ? handleShip : null}');
  });
  it("no longer has its own duplicate Cancel/Ship-it/Rollback/Retry buttons", () => {
    expect(src).not.toContain('data-testid="ship-modal-cancel"');
    expect(src).not.toContain('data-testid="ship-modal-confirm"');
    expect(src).not.toContain('data-testid="ship-modal-rollback"');
    expect(src).not.toContain('data-testid="ship-modal-retry"');
  });
});

describe("R3 — ShipApproveCard itself is built on useShipState (one hook, one card)", () => {
  it("imports useShipState from the shared lib", () => {
    const src = read("../ShipApproveCard.jsx");
    expect(src).toContain('import { useShipState } from "../lib/useShipState"');
  });
});

describe("P2-D (this round) — LoopLiveFeed's ShippedRow is NOW physically merged too", () => {
  it("imports ShipApproveCard and renders it for the rollback row (reversing R3's deferral)", () => {
    const src = read("../LoopLiveFeed.jsx");
    expect(src).toContain('import ShipApproveCard from "./ShipApproveCard"');
    expect(src).toContain("<ShipApproveCard");
    expect(src).toContain('status={phase === "idle" ? "shipped" : phase}');
  });
  it("preserves the Iter-342 pointerdown race-safety via a dedicated prop", () => {
    const src = read("../LoopLiveFeed.jsx");
    expect(src).toContain("onRollbackPointerDown={(e) => {");
    expect(src).toContain('triggerRollback("pointerdown")');
  });
  it("preserves the legacy testid so no existing selector breaks", () => {
    const src = read("../LoopLiveFeed.jsx");
    expect(src).toContain("testidOverrides={{");
    expect(src).toContain("rollback: `loop-shipped-rollback-btn-${ship.shortSha}`");
  });
});
