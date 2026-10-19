/**
 * CodebaseHealth.connectFirst.test.jsx — UI-fix C4 (2026-10)
 *
 * Bug fix: clicking a scan category or Full Scan with no connected
 * project used to drop a plain "Connect a project first from the
 * dashboard." string into the error pill with no way forward. Now it
 * shows a dedicated "Connect a repo first" card whose button opens
 * the existing NewUserWizard connect flow (not a new one).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../../components/BulkFixConfirmModal", () => ({ default: () => null }));
vi.mock("../../components/NewUserWizard", () => ({
  default: ({ onComplete }) => (
    <button
      data-testid="test-wizard-complete"
      onClick={() => { localStorage.setItem("aurem_active_project", "p_new"); onComplete(); }}
    >Finish connect</button>
  ),
}));

import CodebaseHealth from "../CodebaseHealth.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  window.history.replaceState({}, "", "/codebase-health");
  api.get.mockResolvedValue({ data: {} });
});

function renderPage() {
  return render(<MemoryRouter><CodebaseHealth /></MemoryRouter>);
}

describe("UI-fix C4 · project-less scan attempt", () => {
  it("clicking a category with no connected project shows 'Connect a repo first', not a silent error string", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("scan-security"));
    expect(screen.getByTestId("health-connect-first")).toHaveTextContent("Connect a repo first");
    expect(screen.queryByTestId("health-error")).toBeNull();
  });

  it("the card's button opens the existing NewUserWizard connect flow", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("scan-full"));
    await user.click(screen.getByTestId("health-connect-first-btn"));
    expect(screen.getByTestId("test-wizard-complete")).toBeInTheDocument();
  });

  it("completing the wizard picks up the newly-connected project and dismisses the card", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("scan-security"));
    await user.click(screen.getByTestId("health-connect-first-btn"));
    await user.click(screen.getByTestId("test-wizard-complete"));
    await waitFor(() => expect(screen.queryByTestId("health-connect-first")).toBeNull());
    expect(screen.queryByTestId("test-wizard-complete")).toBeNull();
  });
});
