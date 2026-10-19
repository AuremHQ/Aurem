/**
 * BugHunt.authedCta.test.jsx — UI-fix C2 (2026-10)
 *
 * Bug fix: the three CTAs on the /bug-hunt marketing page always sent
 * logged-in users to /signup even though they're already authed. Only
 * admin/founder users ever reach this render (non-admin authed users
 * are redirected to /dashboard upstream), so "isAuthed" here means an
 * admin/founder who should go straight to the real scanner.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import BugHunt from "../BugHunt";
import { getUser, getToken, isAdminOrFounder } from "../../lib/api";

vi.mock("../../lib/api", () => ({
  getUser: vi.fn(),
  getToken: vi.fn(),
  isAdminOrFounder: vi.fn(),
}));

function renderPage() {
  return render(<MemoryRouter><BugHunt /></MemoryRouter>);
}

describe("BugHunt page CTAs by auth state", () => {
  it("logged-out visitor keeps /signup on all three CTAs", () => {
    getToken.mockReturnValue(null);
    getUser.mockReturnValue(null);
    isAdminOrFounder.mockReturnValue(false);
    renderPage();
    expect(screen.getByTestId("bh-nav-signup")).toHaveAttribute("href", "/signup");
    expect(screen.getByTestId("bh-hero-cta")).toHaveAttribute("href", "/signup");
    expect(screen.getByTestId("bh-final-cta")).toHaveAttribute("href", "/signup");
  });

  it("authed admin/founder gets /codebase-health with 'Scan my repo' on all three CTAs", () => {
    getToken.mockReturnValue("tok");
    getUser.mockReturnValue({ id: "u1", is_admin: true });
    isAdminOrFounder.mockReturnValue(true);
    renderPage();
    expect(screen.getByTestId("bh-nav-signup")).toHaveAttribute("href", "/codebase-health");
    expect(screen.getByTestId("bh-nav-signup")).toHaveTextContent("Scan my repo");
    expect(screen.getByTestId("bh-hero-cta")).toHaveAttribute("href", "/codebase-health");
    expect(screen.getByTestId("bh-hero-cta")).toHaveTextContent("Scan my repo");
    expect(screen.getByTestId("bh-final-cta")).toHaveAttribute("href", "/codebase-health");
    expect(screen.getByTestId("bh-final-cta")).toHaveTextContent("Scan my repo");
  });
});
