/**
 * MobileNavSheet.portal_regression.test.jsx
 *
 * 2026-11-24 — real bug, proven live at ~390px: the open mobile menu was
 * translucent — hero text (marquee, "DEVELOPERS CHOICE" pill, "The AI
 * engineer" heading, CTA) showed through it, even though the sheet's own
 * rule was a fully opaque `background: #0a0e1a`.
 *
 * Root cause: MobileNavSheet was rendered as a child of the sticky
 * `<nav class="aurem-marketing-nav">`, which has
 * `backdrop-filter: blur(18px)`. A non-none backdrop-filter makes the
 * element a containing block for fixed-position descendants, so the
 * sheet's `position: fixed; inset: 0` resolved against the ~66px-tall
 * nav box instead of the viewport. The opaque background painted only
 * over the header strip; the menu rows overflowed below it with no
 * background behind them.
 *
 * Fix: render the sheet in a React portal attached to document.body, so
 * `position: fixed` resolves against the viewport and the sheet covers
 * the full screen with its opaque background.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

vi.mock("../../../lib/api", () => ({
  getToken: vi.fn(() => null),
  getUser: vi.fn(() => null),
  isAdminOrFounder: vi.fn(() => false),
}));

import MobileNavSheet from "../MobileNavSheet.jsx";

describe("MobileNavSheet — portal (2026-11-24 menu opacity regression)", () => {
  it("renders the open sheet as a direct child of document.body, not inside the nav", () => {
    render(
      <MemoryRouter>
        <nav className="aurem-marketing-nav">
          <MobileNavSheet open onClose={() => {}} />
        </nav>
      </MemoryRouter>,
    );
    const sheet = screen.getByTestId("mobile-nav-sheet");
    expect(sheet.parentElement).toBe(document.body);
  });

  it("renders nothing when closed", () => {
    render(
      <MemoryRouter>
        <MobileNavSheet open={false} onClose={() => {}} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("mobile-nav-sheet")).toBeNull();
  });
});
