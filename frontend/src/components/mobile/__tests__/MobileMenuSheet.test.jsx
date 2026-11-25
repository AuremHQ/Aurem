/**
 * MobileMenuSheet.test.jsx
 *
 * 2026-11-25 — Mobile nav fix (Teji's phone screenshot): the 56px desktop
 * icon rail showed on phones AND the bottom tab bar showed (Chat/Ship
 * twice, layout squeezed). The rail is hidden on mobile now and its
 * items live in this sheet instead.
 *
 * Proves:
 *   1. Menu sheet carries the relocated rail items (Settings, Analytics,
 *      Secrets vault, AUTO toggle, Log out) — and does NOT duplicate
 *      Chat or Ship (they live on the bottom tab bar).
 *   2. Projects sheet carries the repo switcher; picking a repo calls
 *      onSelectRepo and closes; Add repository calls onAddRepo.
 *   3. AUTO toggle is real: it flips the same localStorage key the
 *      desktop rail uses (`aurem_rail_autohide`).
 *   4. Sheet renders in a portal to document.body; Escape / backdrop /
 *      X close it; closed renders nothing.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../../lib/api", () => ({
  // Default: the gating fetches never resolve, so no async state update
  // fires mid-test (React act warnings). Tests that need a resolution
  // override the implementation and await with findBy*.
  api: { get: vi.fn(() => new Promise(() => {})) },
  getToken: vi.fn(() => "token"),
  logout: vi.fn(),
  fetchMyAdminAccess: vi.fn(() => new Promise(() => {})),
}));
vi.mock("../../../lib/pageFlags", () => ({
  setPageFlags: vi.fn(),
  isAnalyticsPageEnabled: vi.fn(() => true),
}));

import { api, fetchMyAdminAccess } from "../../../lib/api";
import MobileMenuSheet from "../MobileMenuSheet.jsx";

const REPOS = [
  { id: "p1", owner: "TJSNDHU", name: "Aurem", branch: "main", active: true },
  { id: "p2", owner: "TJSNDHU", name: "orbit-payments", branch: "fix/webhook", active: false },
];

const baseProps = {
  onClose: vi.fn(),
  repos: REPOS,
  onSelectRepo: vi.fn(),
  onAddRepo: vi.fn(),
  onNewRun: vi.fn(),
  onLogout: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  // Re-assert the never-resolving defaults (a test that overrides the
  // gating-fetch implementation must not leak it into the next test —
  // a resolving fetch would fire a state update outside act there).
  api.get.mockImplementation(() => new Promise(() => {}));
  fetchMyAdminAccess.mockImplementation(() => new Promise(() => {}));
});

function renderSheet(props) {
  return render(
    <MemoryRouter>
      <MobileMenuSheet {...baseProps} {...props} />
    </MemoryRouter>,
  );
}

describe("MobileMenuSheet — menu (2026-11-25 mobile nav fix)", () => {
  it("renders the relocated rail items and no Chat/Ship duplication", () => {
    renderSheet({ sheet: "menu", open: true });
    expect(screen.getByTestId("mobile-sheet-link-analytics")).toBeTruthy();
    expect(screen.getByTestId("mobile-sheet-link-profile")).toBeTruthy();
    expect(screen.getByTestId("mobile-sheet-link-plans")).toBeTruthy();
    expect(screen.getByTestId("mobile-sheet-link-vault")).toBeTruthy();
    expect(screen.getByTestId("mobile-sheet-auto-toggle")).toBeTruthy();
    expect(screen.getByTestId("mobile-sheet-logout")).toBeTruthy();
    expect(screen.getByTestId("mobile-sheet-new-run")).toBeTruthy();
    // Chat and Ship live on the bottom tab bar — they must not appear here.
    expect(screen.queryByText("Chat")).toBeNull();
    expect(screen.queryByText("Ship")).toBeNull();
  });

  it("renders the sheet in a portal to document.body", () => {
    renderSheet({ sheet: "menu", open: true });
    expect(screen.getByTestId("mobile-menu-sheet").parentElement).toBe(document.body);
  });

  it("renders nothing when closed", () => {
    renderSheet({ sheet: "menu", open: false });
    expect(screen.queryByTestId("mobile-menu-sheet")).toBeNull();
  });

  it("AUTO toggle flips the rail's localStorage key", () => {
    renderSheet({ sheet: "menu", open: true });
    const toggle = screen.getByTestId("mobile-sheet-auto-toggle");
    expect(toggle.textContent).toContain("AUTO");
    fireEvent.click(toggle);
    expect(localStorage.getItem("aurem_rail_autohide")).toBe("0");
    expect(toggle.textContent).toContain("OFF");
    fireEvent.click(toggle);
    expect(localStorage.getItem("aurem_rail_autohide")).toBe("1");
    expect(toggle.textContent).toContain("AUTO");
  });

  it("Log out calls onLogout", () => {
    const onLogout = vi.fn();
    renderSheet({ sheet: "menu", open: true, onLogout });
    fireEvent.click(screen.getByTestId("mobile-sheet-logout"));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("New run calls onNewRun and closes", () => {
    const onNewRun = vi.fn();
    const onClose = vi.fn();
    renderSheet({ sheet: "menu", open: true, onNewRun, onClose });
    fireEvent.click(screen.getByTestId("mobile-sheet-new-run"));
    expect(onNewRun).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape closes, backdrop click closes, X closes", () => {
    const onClose = vi.fn();
    renderSheet({ sheet: "menu", open: true, onClose });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("mobile-menu-sheet-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByTestId("mobile-menu-sheet-close"));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("Admin row appears only for a founder (fail closed otherwise)", async () => {
    // Default mock: the gating fetches never resolve → no Admin row.
    const first = renderSheet({ sheet: "menu", open: true });
    expect(screen.queryByTestId("mobile-sheet-link-admin")).toBeNull();
    first.unmount();

    // Founder: /auth/me returns tier founder → Admin row appears.
    api.get.mockImplementation((url) =>
      url === "/auth/me"
        ? Promise.resolve({ data: { user: { tier: "founder" } } })
        : Promise.resolve({ data: {} }),
    );
    fetchMyAdminAccess.mockResolvedValue(null);
    render(
      <MemoryRouter>
        <MobileMenuSheet {...baseProps} sheet="menu" open />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("mobile-sheet-link-admin")).toBeTruthy();
  });
});

describe("MobileMenuSheet — projects (2026-11-25 mobile nav fix)", () => {
  it("renders the repo switcher; picking a repo selects it and closes", () => {
    const onSelectRepo = vi.fn();
    const onClose = vi.fn();
    renderSheet({ sheet: "projects", open: true, onSelectRepo, onClose });
    const row = screen.getByTestId("mobile-sheet-repo-p2");
    expect(row.textContent).toContain("orbit-payments");
    fireEvent.click(row);
    expect(onSelectRepo).toHaveBeenCalledTimes(1);
    expect(onSelectRepo).toHaveBeenCalledWith(REPOS[1]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Add repository calls onAddRepo and closes; Manage repositories links to /projects", () => {
    const onAddRepo = vi.fn();
    const onClose = vi.fn();
    renderSheet({ sheet: "projects", open: true, onAddRepo, onClose });
    fireEvent.click(screen.getByTestId("mobile-sheet-add-repo"));
    expect(onAddRepo).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("mobile-sheet-manage-repos").getAttribute("href")).toBe("/projects");
  });
});
