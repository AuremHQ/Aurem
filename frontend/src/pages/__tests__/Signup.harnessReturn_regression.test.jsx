/**
 * Signup.harnessReturn_regression.test.jsx
 *
 * 2026-11-16 — a prompt claimed "email signin from /harness landed in
 * the ORA app instead of returning" and asked to fix it across email,
 * Google, and GitHub. Direct investigation found this already fixed
 * (Fix 7/8, 2026-11-11): Signup.jsx's `next` state already defaults to
 * `?next=` and `navigate(next, { replace: true })` already runs after
 * a real signup (Signup.jsx line ~148). This test proves that
 * end-to-end rather than trusting the source read — a real signup
 * submitted with `?next=/harness` must land the user back on
 * `/harness`, not /dashboard and not anywhere in the ORA app shell.
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/api", () => ({
  api: {
    get: vi.fn(() => Promise.reject(new Error("no robot-guide in test"))),
    post: vi.fn(),
  },
  setToken: vi.fn(),
  setUser: vi.fn(),
}));

import Signup from "../Signup";
import { api } from "../../lib/api";

function renderSignupAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/signup" element={<Signup />} />
        <Route path="/harness" element={<div data-testid="landed-on-harness">harness page</div>} />
        <Route path="/dashboard" element={<div data-testid="landed-on-dashboard">ORA dashboard</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.matchMedia = window.matchMedia || function matchMedia(query) {
    return { matches: false, media: query, addListener: () => {}, removeListener: () => {} };
  };
});

describe("Signup — return to /harness after a real signup (not the ORA app)", () => {
  it("a signup submitted with ?next=/harness lands on /harness, not /dashboard", async () => {
    api.post.mockImplementation((path) => {
      if (path === "/auth/signup") {
        return Promise.resolve({
          data: {
            token: "fake-jwt", user_id: "u1", email: "new@example.com",
            name: "New User", tier: "free", tokens_remaining: 1000,
            created_at: "2026-11-16T00:00:00Z",
          },
        });
      }
      return Promise.reject(new Error(`unexpected post ${path}`));
    });

    renderSignupAt("/signup?next=/harness");

    fireEvent.change(screen.getByTestId("signup-email"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByTestId("signup-password"), { target: { value: "TestPass123!" } });
    fireEvent.change(screen.getByTestId("signup-password-confirm"), { target: { value: "TestPass123!" } });
    fireEvent.click(screen.getByTestId("signup-terms-checkbox"));
    fireEvent.click(screen.getByTestId("signup-submit"));

    await waitFor(() => expect(screen.getByTestId("landed-on-harness")).toBeInTheDocument());
    expect(screen.queryByTestId("landed-on-dashboard")).not.toBeInTheDocument();
  });

  it("tags the signup request with source=harness_homepage when arriving via ?next=/harness", async () => {
    api.post.mockResolvedValue({
      data: {
        token: "fake-jwt", user_id: "u1", email: "new@example.com",
        name: "New User", tier: "free", tokens_remaining: 1000,
        created_at: "2026-11-16T00:00:00Z",
      },
    });

    renderSignupAt("/signup?next=/harness");

    fireEvent.change(screen.getByTestId("signup-email"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByTestId("signup-password"), { target: { value: "TestPass123!" } });
    fireEvent.change(screen.getByTestId("signup-password-confirm"), { target: { value: "TestPass123!" } });
    fireEvent.click(screen.getByTestId("signup-terms-checkbox"));
    fireEvent.click(screen.getByTestId("signup-submit"));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/auth/signup", expect.objectContaining({ source: "harness_homepage" }),
    ));
  });

  it("a signup with no ?next= (arriving from ORA itself) is NOT tagged with the harness source", async () => {
    api.post.mockResolvedValue({
      data: {
        token: "fake-jwt", user_id: "u1", email: "new@example.com",
        name: "New User", tier: "free", tokens_remaining: 1000,
        created_at: "2026-11-16T00:00:00Z",
      },
    });

    renderSignupAt("/signup");

    fireEvent.change(screen.getByTestId("signup-email"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByTestId("signup-password"), { target: { value: "TestPass123!" } });
    fireEvent.change(screen.getByTestId("signup-password-confirm"), { target: { value: "TestPass123!" } });
    fireEvent.click(screen.getByTestId("signup-terms-checkbox"));
    fireEvent.click(screen.getByTestId("signup-submit"));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/auth/signup", expect.anything()));
    const [, body] = api.post.mock.calls.find(([path]) => path === "/auth/signup");
    expect(body.source).toBeUndefined();
  });
});
