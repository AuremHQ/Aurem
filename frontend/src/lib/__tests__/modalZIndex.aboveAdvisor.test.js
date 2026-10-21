/**
 * modalZIndex.aboveAdvisor.test.js — UI-fix D4 (2026-10)
 *
 * Bug fix: NewUserWizard (z-index 9000) and StarterChoiceModal
 * (9100) were both hardcoded BELOW Z_ADVISOR_TOGGLE (10002), so the
 * floating Advisor re-open tab stayed clickable/tabbable on top of
 * these "blocking" overlays. Both now use the shared Z_ACTIVE_MODAL
 * constant, which must sit above the Advisor toggle.
 */
import { describe, it, expect } from "vitest";
import * as zIndex from "../zIndex";

describe("UI-fix D4 · modal overlays render above the Advisor toggle", () => {
  it("Z_ACTIVE_MODAL (used by NewUserWizard + StarterChoiceModal) is above Z_ADVISOR_TOGGLE", () => {
    expect(zIndex.Z_ACTIVE_MODAL).toBeGreaterThan(zIndex.Z_ADVISOR_TOGGLE);
  });
});
