/**
 * CodebaseHealth.githubReconnectBanner.test.jsx — UI-fix C6 (2026-10)
 *
 * Bug fix: a 409 {code: "github_install_unreachable", detail} scan
 * error used to be thrown straight at the generic error pill as an
 * object (React can't render an object child). It now shows a
 * dedicated banner with a working "Reconnect GitHub" action.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../../components/BulkFixConfirmModal", () => ({ default: () => null }));
vi.mock("../../components/NewUserWizard", () => ({ default: () => null }));

import CodebaseHealth from "../CodebaseHealth.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/codebase-health?project_id=p1");
  api.get.mockResolvedValue({ data: {} });
});

it("shows a Reconnect GitHub banner on a 409 github_install_unreachable, and the button navigates to the repair flow", async () => {
  api.post.mockRejectedValueOnce({
    response: { status: 409, data: { detail: {
      code: "github_install_unreachable",
      detail: "GitHub returned HTTP 404 while minting the App installation token. Retry shortly.",
    } } },
  });
  render(<MemoryRouter><CodebaseHealth /></MemoryRouter>);
  fireEvent.click(await screen.findByTestId("scan-security"));

  await waitFor(() => expect(screen.getByTestId("health-github-unreachable")).toBeInTheDocument());
  expect(screen.getByTestId("health-github-unreachable")).toHaveTextContent("Retry shortly");
  expect(screen.queryByTestId("health-error")).toBeNull();

  fireEvent.click(screen.getByTestId("health-github-reconnect-btn"));
  expect(navigateMock).toHaveBeenCalledWith("/projects?pat=p1");
});
