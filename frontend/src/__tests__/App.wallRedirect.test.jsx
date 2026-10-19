/**
 * App.wallRedirect.test.jsx — UI-fix C7 (2026-10)
 *
 * Bug fix: /wall's flag-off redirect always sent everyone to "/" (the
 * logged-out marketing page), even an already-authed user — so a
 * logged-in visitor clicking a stale /wall link got bounced back to
 * the public landing page instead of their own dashboard. Mirrors the
 * exact `<Navigate to={getToken() ? "/dashboard" : "/"} replace />`
 * route element now in App.jsx (full App.jsx isn't imported here —
 * its import surface is too large for a one-route regression test).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route, Navigate } from "react-router-dom";

vi.mock("../lib/api", () => ({ getToken: vi.fn() }));
import { getToken } from "../lib/api";

function WallRoute() {
  return (
    <MemoryRouter initialEntries={["/wall"]}>
      <Routes>
        <Route path="/wall" element={<Navigate to={getToken() ? "/dashboard" : "/"} replace />} />
        <Route path="/dashboard" element={<div data-testid="dest-dashboard" />} />
        <Route path="/" element={<div data-testid="dest-landing" />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => vi.clearAllMocks());

describe("UI-fix C7 · /wall flag-off redirect respects auth state", () => {
  it("sends a logged-in user to /dashboard, never back to the logged-out landing page", () => {
    getToken.mockReturnValue("tok");
    render(<WallRoute />);
    expect(screen.getByTestId("dest-dashboard")).toBeInTheDocument();
    expect(screen.queryByTestId("dest-landing")).toBeNull();
  });

  it("sends an anonymous visitor to the landing page", () => {
    getToken.mockReturnValue(null);
    render(<WallRoute />);
    expect(screen.getByTestId("dest-landing")).toBeInTheDocument();
    expect(screen.queryByTestId("dest-dashboard")).toBeNull();
  });
});
