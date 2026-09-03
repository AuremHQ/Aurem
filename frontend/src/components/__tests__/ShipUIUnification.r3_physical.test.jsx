/**
 * ShipUIUnification.r3_physical.test.jsx — R3 (this round).
 *
 * Proves the PHYSICAL merge (not just shared labels): ShipDialog
 * (chat-inline) and ShipConfirmModal (modal) both actually import and
 * render <ShipApproveCard/>, which itself is built on useShipState().
 * LoopLiveFeed's ShippedRow is a documented, deliberate exception
 * (see comment below) — it tags its canonical status via
 * toCanonicalShipStatus() instead of swapping its button, because its
 * click-handling has fixed 3 real production incidents (Iter 329,
 * 342, 362) that a generic card would risk reintroducing.
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

describe("R3 — LoopLiveFeed's ShippedRow: documented scope line, not a silent skip", () => {
  const src = read("../LoopLiveFeed.jsx");
  it("still tags its canonical status via the shared toCanonicalShipStatus mapper", () => {
    expect(src).toContain('import { toCanonicalShipStatus } from "../lib/useShipState"');
    expect(src).toContain("data-canonical-ship-status={toCanonicalShipStatus(phase)}");
  });
});
