/**
 * OraGuideMascot.zeroProjectCrash.test.jsx — regression
 * test_regression_iter2026_10z_mascot_hook_order_zero_project_crash
 *
 * Bug report: an account with ZERO connected projects (teji.ss1986@
 * gmail.com, reproduced live with a throwaway 0-project admin test
 * account) could not land on /dashboard OR /admin — both rendered a
 * completely blank page. Browser console showed:
 *   "Warning: React has detected a change in the order of Hooks
 *    called by OraGuideMascot... Rendered more hooks than during the
 *    previous render."
 *
 * Root cause: `if (hidden) return null;` used to sit BEFORE the
 * Escape-key `useEffect` in OraGuideMascot.jsx. `hidden` depends on
 * `useCanShowOverlay("mascot")`, which the mandatory 0-project
 * repo-connect gate (a higher-priority overlay, see
 * hooks/useOverlaySlot.js) flips false->true/true->false as it
 * mounts/settles — something that ONLY happens for a 0-project user
 * (a user with >=1 project never triggers that gate, so `hidden`
 * never changes and the bug never fires for them). When `hidden`
 * flipped, OraGuideMascot called a different NUMBER of hooks between
 * renders — a Rules-of-Hooks violation with no recovery, crashing
 * the whole app (OraGuideMascot renders globally inside
 * MaintenanceGate, above both /dashboard and /admin).
 *
 * Fix: the Escape-key effect now runs unconditionally before the
 * early return, like every other hook in this component.
 *
 * This test uses the REAL useOverlaySlot/useCanShowOverlay module
 * (not mocked) and a sibling component that claims the
 * wizard-priority slot AFTER the first render, forcing exactly the
 * `hidden` false->true transition that crashed the app — same
 * mechanism any 0-project account hits, not just this one admin.
 */
import React, { useState } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useOverlaySlot } from "../../hooks/useOverlaySlot";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(() => Promise.resolve({ data: {} })) } }));
vi.mock("../TabBar", () => ({ useActiveProject: () => null, setActiveProjectId: vi.fn() }));
vi.mock("../GlobalHelpFAB", () => ({ shouldHide: () => false }));
vi.mock("../Toast", () => ({ toast: vi.fn() }));
vi.mock("../../hooks/useGuideSpotlight.jsx", () => ({
  useGuideSpotlight: () => {}, GuideSpotlightStyle: () => null,
}));

import OraGuideMascot from "../OraGuideMascot";

// Stands in for the mandatory 0-project repo-connect gate: claims the
// higher-priority "wizard" overlay slot only once `claimed` is true,
// exactly like the real gate claims it once it decides to render.
function FakeWizardOverlay({ claimed }) {
  useOverlaySlot("wizard", claimed);
  return null;
}

function Harness() {
  const [wizardOpen, setWizardOpen] = useState(false);
  return (
    <MemoryRouter initialEntries={["/dashboard"]}>
      <FakeWizardOverlay claimed={wizardOpen} />
      <OraGuideMascot />
      <button data-testid="open-wizard" onClick={() => setWizardOpen(true)}>
        open wizard
      </button>
      <button data-testid="close-wizard" onClick={() => setWizardOpen(false)}>
        close wizard
      </button>
    </MemoryRouter>
  );
}

describe("regression iter2026-10z · zero-project hook-order crash", () => {
  let consoleErrorSpy;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("aurem_token", "tok");
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  it("does not crash when a higher-priority overlay (the 0-project gate) " +
     "claims the slot after the mascot's first render", async () => {
    render(<Harness />);

    // Mascot visible first (no overlay competing — same as the
    // moment right after the page first paints).
    await screen.findByTestId("ora-guide-mascot-avatar");

    // The 0-project repo-connect gate "opens" — this flips
    // useCanShowOverlay("mascot") false, and `hidden` true, on the
    // mascot's NEXT render. Pre-fix, this is exactly what threw
    // "Rendered more hooks than during the previous render."
    act(() => { fireEvent.click(screen.getByTestId("open-wizard")); });

    // Mascot must now be hidden (gate has priority) — but hidden via
    // a clean `return null`, not a crash.
    expect(screen.queryByTestId("ora-guide-mascot-avatar")).toBeNull();

    // Gate closes — mascot must come back, proving the flip survived
    // round-trip with no hook-count mismatch either direction.
    act(() => { fireEvent.click(screen.getByTestId("close-wizard")); });
    await screen.findByTestId("ora-guide-mascot-avatar");

    const hookOrderError = consoleErrorSpy.mock.calls.some((args) =>
      args.some((a) => typeof a === "string" &&
        (a.includes("Rendered more hooks") || a.includes("order of Hooks"))));
    expect(hookOrderError).toBe(false);
  });
});
