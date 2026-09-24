import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
vi.mock("../../components/Shell", () => ({ default: ({ children }) => <>{children}</>, PageHeader: () => null }));
vi.mock("../../components/nav/RailShell", () => ({ default: ({ children }) => <>{children}</> }));
vi.mock("../../components/FounderOfferPill", () => ({ default: () => null }));
vi.mock("../../components/ArchitectBlueprintPanel", () => ({ default: () => null }));
vi.mock("../../components/Toast", () => ({ toast: vi.fn() }));
vi.mock("../../lib/api", () => ({ api: { get: vi.fn() }, getToken: () => "test", API_BASE: "https://api.test" }));
vi.mock("../../components/AddProjectWizard", () => ({
  default: ({ onAdded, stayOnProjects }) => <button data-testid="test-return-wizard"
    data-stay={String(stayOnProjects)} onClick={() => onAdded("p_new")}>Add</button>,
}));
import Projects from "../Projects";
import { api } from "../../lib/api";
const old = { project_id: "p_old", name: "Old", github_owner: "team", github_repo: "old" };
const added = { project_id: "p_new", name: "New", github_owner: "team", github_repo: "new" };
beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/projects?flow=connect-repo&install=success&install_id=10");
  api.get.mockResolvedValue({ data: { projects: [old], tasks: [] } });
});
afterEach(() => window.history.replaceState({}, "", "/"));

it("opens the return wizard even with existing projects, then refreshes and selects the saved project", async () => {
  render(<Projects />);
  await screen.findByTestId("proj-row-p_old");
  const wizard = screen.getByTestId("test-return-wizard");
  expect(wizard.dataset.stay).toBe("true");
  api.get.mockResolvedValue({ data: { projects: [old, added], tasks: [] } });
  fireEvent.click(wizard);
  await screen.findByTestId("proj-row-p_new");
  await waitFor(() => expect(screen.queryByTestId("test-return-wizard")).toBeNull());
  expect(screen.queryByTestId("proj-empty")).toBeNull();
});