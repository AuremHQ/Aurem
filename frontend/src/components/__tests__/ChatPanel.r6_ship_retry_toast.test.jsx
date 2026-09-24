/**
 * R6 (overnight round) — one-click Retry on the ship-failure toast +
 * ShipConfirmModal's error-phase Retry button.
 *
 * Source-scan style (matches this codebase's existing convention for
 * label/behavior fixes, e.g. ShipConfirmModal.p1b_honest_label.test.jsx)
 * — avoids a brittle full-DOM render of these very large components.
 */
import { readFileSync } from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const CHAT_PANEL_SRC = readFileSync(
  path.resolve(__dirname, "../ChatPanel.jsx"), "utf-8"
);
const SHIP_MODAL_SRC = readFileSync(
  path.resolve(__dirname, "../ShipConfirmModal.jsx"), "utf-8"
);

describe("ChatPanel — ship-failure toast has a Retry action (R6)", () => {
  it("handleShipConfirm's catch offers a Retry action that re-calls handleShipConfirm", () => {
    const idx = CHAT_PANEL_SRC.indexOf("Ship failed to start");
    expect(idx).toBeGreaterThan(-1);
    const nearby = CHAT_PANEL_SRC.slice(idx, idx + 400);
    expect(nearby).toContain('label: "Retry"');
    expect(nearby).toContain("handleShipConfirm()");
  });
});

describe("ShipConfirmModal — error phase has a Retry button (R6)", () => {
  it("renders a Retry button via the shared ShipApproveCard, wired to handleShip", () => {
    // R3 (this round) — the Retry button physically moved into the
    // shared <ShipApproveCard/> (same component ShipDialog uses).
    // ShipConfirmModal now passes onRetry={handleShip} down to it.
    expect(SHIP_MODAL_SRC).toContain('onRetry={phase === "error" ? handleShip : null}');
    expect(SHIP_MODAL_SRC).toContain("<ShipApproveCard");
    const cardSrc = readFileSync(
      path.resolve(__dirname, "../ShipApproveCard.jsx"), "utf-8"
    );
    expect(cardSrc).toContain("const retryTestid = testidOverrides.retry ||");
    expect(cardSrc).toContain('`${testidPrefix}-retry-btn`');
    expect(cardSrc).toContain("onClick={onRetry}");
  });

  it("handleShip allows re-entry from the error phase (not just confirm)", () => {
    expect(SHIP_MODAL_SRC).toContain('phase !== "confirm" && phase !== "error"');
  });
});
