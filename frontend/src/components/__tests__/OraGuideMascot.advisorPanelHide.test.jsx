// OraGuideMascot.advisorPanelHide.test.jsx - Minor 11 + Minor 16
// Minor 11: the floating guide button hides while the Advisor panel
// is open, so it never covers the Advisor input/send row.
// Minor 16: the guide panel never grows into the dashboard top bar.
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../GlobalHelpFAB", () => ({ shouldHide: () => false }));
vi.mock("../Toast", () => ({ toast: vi.fn() }));
vi.mock("../../hooks/useGuideSpotlight.jsx", () => ({
  useGuideSpotlight: () => {},
  GuideSpotlightStyle: () => null,
}));

import OraGuideMascot from "../OraGuideMascot";

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.setItem("aurem_token", "tok");
});

function renderMascot() {
  render(<MemoryRouter initialEntries={["/dashboard"]}><OraGuideMascot /></MemoryRouter>);
}

describe("Minor 11 - floating guide button hides while the Advisor panel is open", () => {
  it("hides the launcher on aurem:ora-panel-state open and shows it again on close", async () => {
    renderMascot();
    await screen.findByTestId("ora-guide-mascot-avatar");

    window.dispatchEvent(new CustomEvent("aurem:ora-panel-state", { detail: { open: true } }));
    await waitFor(() => expect(screen.queryByTestId("ora-guide-mascot-avatar")).toBeNull());
    expect(screen.queryByTestId("ora-guide-mascot-root")).toBeNull();

    window.dispatchEvent(new CustomEvent("aurem:ora-panel-state", { detail: { open: false } }));
    await screen.findByTestId("ora-guide-mascot-avatar");
  });
});

describe("Minor 16 - guide panel never reaches the dashboard top bar", () => {
  it("caps the open panel height to leave the header clear", async () => {
    renderMascot();
    const avatar = await screen.findByTestId("ora-guide-mascot-avatar");
    fireEvent.click(avatar);
    const bubble = await screen.findByTestId("ora-guide-bubble");
    expect(bubble.style.maxHeight).toContain("calc(100vh - 248px)");
    expect(bubble.style.overflowY).toBe("auto");
  });
});
