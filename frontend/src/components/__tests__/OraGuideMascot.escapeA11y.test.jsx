/**
 * OraGuideMascot.escapeA11y.test.jsx — UI-fix D4 (2026-10)
 *
 * The bubble isn't a true modal dialog (it never blocks the rest of
 * the page), so it uses role="status" rather than role="dialog" — but
 * it must still close on Escape and return focus to the toggle
 * button, matching the two real modals' contract.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(() => Promise.resolve({ data: {} })) } }));
vi.mock("../TabBar", () => ({ useActiveProject: () => null, setActiveProjectId: vi.fn() }));
vi.mock("../GlobalHelpFAB", () => ({ shouldHide: () => false }));
vi.mock("../Toast", () => ({ toast: vi.fn() }));
vi.mock("../../hooks/useGuideSpotlight.jsx", () => ({
  useGuideSpotlight: () => {}, GuideSpotlightStyle: () => null,
}));

import OraGuideMascot from "../OraGuideMascot";

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.setItem("aurem_token", "tok");
});

describe("UI-fix D4 · ORA Guide bubble a11y", () => {
  it("the open bubble is a non-modal status region", async () => {
    render(<MemoryRouter initialEntries={["/dashboard"]}><OraGuideMascot /></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("ora-guide-mascot-avatar"));
    const bubble = await screen.findByTestId("ora-guide-bubble");
    expect(bubble).toHaveAttribute("role", "status");
  });

  it("Escape closes the bubble and returns focus to the toggle button", async () => {
    render(<MemoryRouter initialEntries={["/dashboard"]}><OraGuideMascot /></MemoryRouter>);
    const avatar = await screen.findByTestId("ora-guide-mascot-avatar");
    fireEvent.click(avatar);
    await screen.findByTestId("ora-guide-bubble");

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(screen.queryByTestId("ora-guide-bubble")).toBeNull());
    expect(document.activeElement).toBe(avatar);
  });
});
