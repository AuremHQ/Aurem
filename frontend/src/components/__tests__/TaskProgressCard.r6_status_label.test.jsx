/**
 * TaskProgressCard.r6_status_label.test.jsx — R6 ship-UI unification
 * (overnight round).
 *
 * TaskProgressCard (used inside ShipDialog) said "Reverted" while
 * ShipConfirmModal / LoopLiveFeed already said "Rolled back" for the
 * identical underlying state — completing the canonical-label rollout
 * to the 4th real ship surface.
 */
import { readFileSync } from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const SRC = readFileSync(
  path.resolve(__dirname, "../TaskProgressCard.jsx"), "utf-8"
);

describe("TaskProgressCard — canonical 'Rolled back' label (R6)", () => {
  it("no longer says the old 'Reverted' label", () => {
    expect(SRC).not.toContain('"↩︎ Reverted"');
  });

  it("uses the shared shipStatus canonical label helper", () => {
    expect(SRC).toContain('import { shipStatusLabel, SHIP_STATUS } from "../lib/shipStatus"');
    expect(SRC).toContain("shipStatusLabel(SHIP_STATUS.ROLLED_BACK)");
    expect(SRC).toContain("shipStatusLabel(SHIP_STATUS.ROLLING_BACK)");
  });
});
