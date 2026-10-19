/**
 * Projects.loadStates.test.jsx — UI-fix C3 (2026-10)
 *
 * Bug fix: a failed /cto/projects/list fetch used to silently fall
 * through to the "No projects yet. Connect a GitHub repo..." empty
 * state (because `projects` stayed []), even though the user may
 * already have projects connected. Now a failed fetch shows a
 * distinct error card with Retry, and only a genuinely empty list
 * shows "No projects yet".
 */
import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, it, expect, vi } from "vitest";

vi.mock("../../components/Shell", () => ({ default: ({ children }) => <>{children}</>, PageHeader: () => null }));
vi.mock("../../components/nav/RailShell", () => ({ default: ({ children }) => <>{children}</> }));
vi.mock("../../components/FounderOfferPill", () => ({ default: () => null }));
vi.mock("../../components/ArchitectBlueprintPanel", () => ({ default: () => null }));
vi.mock("../../components/Toast", () => ({ toast: vi.fn() }));
vi.mock("../../lib/api", () => ({ api: { get: vi.fn() }, getToken: () => "test", API_BASE: "https://api.test" }));
vi.mock("../../components/AddProjectWizard", () => ({ default: () => null }));

import Projects from "../Projects";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/projects");
});

it("shows an error card with Retry when the project list fetch fails, not 'No projects yet'", async () => {
  api.get.mockRejectedValueOnce(new Error("network down"));
  render(<Projects />);
  await screen.findByTestId("proj-load-error");
  expect(screen.queryByTestId("proj-empty-add-btn")).toBeNull();

  api.get.mockResolvedValueOnce({ data: { projects: [], tasks: [] } });
  fireEvent.click(screen.getByTestId("proj-load-error-retry"));
  await waitFor(() => expect(screen.queryByTestId("proj-load-error")).toBeNull());
});

it("shows 'No projects yet' only when the fetch succeeds with an empty list", async () => {
  api.get.mockResolvedValueOnce({ data: { projects: [], tasks: [] } });
  render(<Projects />);
  await screen.findByTestId("proj-empty-add-btn");
  expect(screen.queryByTestId("proj-load-error")).toBeNull();
});
