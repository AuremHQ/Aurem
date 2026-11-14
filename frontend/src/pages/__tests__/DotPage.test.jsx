/**
 * DotPage.test.jsx — ORA dot homepage, 2026-11-14.
 *
 * The /dot page has NO download (product Phase 0-1 not built) — it
 * explains what ORA dot is and collects a name + email list via
 * POST /api/dot/signup. These tests lock in:
 * - hero + join-the-list form render, with NO download button/link anywhere.
 * - submitting the form posts {name, email} and shows an on-page confirmation.
 * - invalid input shows an inline error and posts nothing.
 * - the signin/signup links carry ?next=/dot, so the existing auth
 *   flow returns the visitor to /dot (Fix 7 gating).
 * - the page renders the site's real MarketingNav, branded ORA dot.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  healthApi: { get: vi.fn(), post: vi.fn() },
  api: { post: vi.fn() },
  getToken: vi.fn(() => null),
  getUser: vi.fn(() => null),
  isAdminOrFounder: vi.fn(() => false),
}));

import DotPage from "../DotPage.jsx";
import { healthApi } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/dot"]}>
      <Routes>
        <Route path="/dot" element={<DotPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DotPage", () => {
  it("renders the hero and the join-the-list form with NO download anywhere", () => {
    renderPage();
    expect(screen.getByTestId("dot-page")).toBeInTheDocument();
    expect(screen.getByTestId("dot-h1")).toHaveTextContent("Your AI workspace, on your own machine.");
    expect(screen.getByTestId("dot-signup-form")).toBeInTheDocument();
    expect(screen.getByTestId("dot-signup-name")).toBeInTheDocument();
    expect(screen.getByTestId("dot-signup-email")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("says plainly there is no download yet", () => {
    renderPage();
    expect(screen.getByText(/No download yet/i)).toBeInTheDocument();
  });

  it("submitting the form posts name + email and shows an on-page confirmation", async () => {
    healthApi.post.mockResolvedValue({ data: { ok: true } });
    renderPage();

    fireEvent.change(screen.getByTestId("dot-signup-name"), { target: { value: "Teji" } });
    fireEvent.change(screen.getByTestId("dot-signup-email"), { target: { value: "dev@example.com" } });
    fireEvent.click(screen.getByTestId("dot-signup-submit"));

    await waitFor(() => expect(screen.getByTestId("dot-signup-success")).toBeInTheDocument());
    expect(healthApi.post).toHaveBeenCalledWith("/dot/signup", { name: "Teji", email: "dev@example.com" });
  });

  it("shows an inline error and posts nothing for an invalid email", async () => {
    renderPage();

    fireEvent.change(screen.getByTestId("dot-signup-name"), { target: { value: "Teji" } });
    fireEvent.change(screen.getByTestId("dot-signup-email"), { target: { value: "not-an-email" } });
    fireEvent.click(screen.getByTestId("dot-signup-submit"));

    await waitFor(() => expect(screen.getByTestId("dot-signup-error")).toBeInTheDocument());
    expect(healthApi.post).not.toHaveBeenCalled();
  });

  it("shows an inline error and posts nothing for a blank name", async () => {
    renderPage();

    fireEvent.change(screen.getByTestId("dot-signup-email"), { target: { value: "dev@example.com" } });
    fireEvent.click(screen.getByTestId("dot-signup-submit"));

    await waitFor(() => expect(screen.getByTestId("dot-signup-error")).toBeInTheDocument());
    expect(healthApi.post).not.toHaveBeenCalled();
  });

  it("the signin and signup links carry ?next=/dot so auth returns here", () => {
    renderPage();
    expect(screen.getByTestId("dot-signin-link")).toHaveAttribute("href", "/login?next=%2Fdot");
    expect(screen.getByTestId("dot-signup-link")).toHaveAttribute("href", "/signup?next=%2Fdot");
  });

  it("renders the site's real MarketingNav, branded ORA dot", () => {
    renderPage();
    expect(screen.getByTestId("ora-nav")).toBeInTheDocument();
    expect(screen.getByTestId("marketing-nav-logo")).toHaveTextContent("ORA");
    expect(screen.getByTestId("marketing-nav-logo")).toHaveTextContent("dot");
  });
});
