/**
 * Projects.changesLink.test.jsx — Part 2c B4.
 *
 * The per-project "Changes" history link is flag-gated
 * (isProjectVersionsEnabled, default OFF) — hidden entirely when off,
 * visible and pointing at the right route when on.
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, it, expect, vi } from "vitest";

vi.mock("../../components/Shell", () => ({ default: ({ children }) => <>{children}</>, PageHeader: () => null }));
vi.mock("../../components/nav/RailShell", () => ({ default: ({ children }) => <>{children}</> }));
vi.mock("../../components/FounderOfferPill", () => ({ default: () => null }));
vi.mock("../../components/ArchitectBlueprintPanel", () => ({ default: () => null }));
vi.mock("../../components/Toast", () => ({ toast: vi.fn() }));
vi.mock("../../lib/api", () => ({ api: { get: vi.fn() }, getToken: () => "test", API_BASE: "https://api.test" }));
vi.mock("../../components/AddProjectWizard", () => ({ default: () => null }));

let _enabled = false;
vi.mock("../../lib/pageFlags", () => ({
  isProjectVersionsEnabled: () => _enabled,
}));

import Projects from "../Projects";
import { api } from "../../lib/api";

const ONE_PROJECT = {
  projects: [{ project_id: "p1", name: "Demo", github_owner: "o", github_repo: "r" }],
  tasks: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  _enabled = false;
  window.history.replaceState({}, "", "/projects");
});

it("hides the Changes link entirely when the flag is off (default)", async () => {
  api.get.mockResolvedValueOnce({ data: ONE_PROJECT });
  render(<MemoryRouter><Projects /></MemoryRouter>);
  await screen.findByTestId("proj-row-p1");
  expect(screen.queryByTestId("proj-row-changes-p1")).toBeNull();
});

it("shows the Changes link pointing at /projects/:id/changes when the flag is on", async () => {
  _enabled = true;
  api.get.mockResolvedValueOnce({ data: ONE_PROJECT });
  render(<MemoryRouter><Projects /></MemoryRouter>);
  await waitFor(() => expect(screen.getByTestId("proj-row-changes-p1")).toBeInTheDocument());
  expect(screen.getByTestId("proj-row-changes-p1").getAttribute("href")).toBe("/projects/p1/changes");
});
