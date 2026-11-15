/**
 * OAuthFinish.next.test.jsx — Fix 7 (2026-11-11) regression.
 *
 * The OAuth success redirect lands here with the JWT + an optional
 * `next` in the URL fragment (`#token=...&next=/harness`). This page
 * must navigate to that `next` instead of always hardcoding
 * /dashboard — and must re-validate it (same-origin relative path
 * only) even though the backend already validated it before storing
 * it, matching the defense-in-depth pattern used everywhere else in
 * this codebase (Login.jsx/Signup.jsx re-check `?next=` the same way).
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("../../lib/api", () => ({
  setToken: vi.fn(),
  setUser: vi.fn(),
  api: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn(() => Promise.resolve({})) },
}));

vi.mock("../../lib/analytics", () => ({
  trackSignup: vi.fn(),
  metaCompleteRegistration: vi.fn(),
}));

import OAuthFinish from "../OAuthFinish";

function renderWithHash(hash) {
  window.location.hash = hash;
  return render(
    <MemoryRouter>
      <OAuthFinish />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = "";
});

describe("OAuthFinish — Fix 7 next-redirect", () => {
  it("navigates to a valid ?next= path from the fragment (e.g. /harness)", async () => {
    renderWithHash("#token=abc123&login=octocat&new=0&next=%2Fharness");
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/harness", { replace: true }));
  });

  it("defaults to /dashboard when no next is present (unchanged old behavior)", async () => {
    renderWithHash("#token=abc123&login=octocat&new=0");
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true }));
  });

  it("falls back to /dashboard for an absolute-URL next (open-redirect attempt)", async () => {
    renderWithHash("#token=abc123&login=octocat&new=0&next=https%3A%2F%2Fevil.com");
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true }));
  });

  it("falls back to /dashboard for a protocol-relative next (//evil.com)", async () => {
    renderWithHash("#token=abc123&login=octocat&new=0&next=%2F%2Fevil.com");
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true }));
  });

  it("still redirects to /login with missing_token when no token is present", async () => {
    renderWithHash("#next=%2Fharness");
    await waitFor(() => expect(screen.getByTestId("oauth-finish-status")).toHaveTextContent(/No sign-in token/i));
  });
});
