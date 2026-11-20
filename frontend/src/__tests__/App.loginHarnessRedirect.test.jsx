/**
 * App.loginHarnessRedirect.test.jsx — /login?next=/harness redirects to /harness (2026-11-20).
 *
 * /login with a harness ?next= value must send the user to the harness's own
 * sign-in/sign-up form (Midnight Gold, tabs there), never to the shared ORA
 * login page. Every other /login use stays exactly as it is.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

vi.mock("../lib/api", () => ({
  getToken: vi.fn(() => null),
  api: {
    get: vi.fn(() => Promise.reject(new Error("no robot-guide in test"))),
    post: vi.fn(() => Promise.resolve({ data: {} })),
  },
  setToken: vi.fn(),
  setUser: vi.fn(),
}));

import { LoginRoute } from "../App";

// App.jsx relies on the automatic JSX runtime, so it does not import React.
// Vitest transforms JSX with the classic runtime (React.createElement), so
// LoginRoute's JSX needs React in scope. Provide it as a global before
// rendering the real LoginRoute; the vite build's automatic runtime ignores it.
globalThis.React = React;

function HarnessDestination() {
  const location = useLocation();
  return <div data-testid="dest-harness">{location.search}</div>;
}

function renderLoginRoute(entry) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/login" element={<LoginRoute />} />
        <Route path="/harness" element={<HarnessDestination />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // Login/AuthShell reads the viewport (responsive background) on mount;
  // jsdom does not implement matchMedia, so provide the same stub the
  // HarnessPage tests use.
  window.matchMedia = window.matchMedia || function matchMedia(query) {
    return { matches: false, media: query, addListener: () => {}, removeListener: () => {} };
  };
});

describe("App — /login with ?next=/harness redirects to /harness", () => {
  it("redirects /login?next=/harness to /harness on the Sign-in tab", async () => {
    renderLoginRoute("/login?next=%2Fharness");
    await waitFor(() => expect(screen.getByTestId("dest-harness")).toBeInTheDocument());
    expect(screen.getByTestId("dest-harness")).toHaveTextContent("tab=signin");
    expect(screen.queryByTestId("login-card")).not.toBeInTheDocument();
  });

  it("keeps the harness query and adds tab=signin for /login?next=/harness?download=1", async () => {
    renderLoginRoute("/login?next=%2Fharness%3Fdownload%3D1");
    await waitFor(() => expect(screen.getByTestId("dest-harness")).toBeInTheDocument());
    expect(screen.getByTestId("dest-harness")).toHaveTextContent("download=1");
    expect(screen.getByTestId("dest-harness")).toHaveTextContent("tab=signin");
    expect(screen.queryByTestId("login-card")).not.toBeInTheDocument();
  });

  it("still renders the shared ORA login page for /login with no next", async () => {
    renderLoginRoute("/login");
    await waitFor(() => expect(screen.getByTestId("login-card")).toBeInTheDocument());
    expect(screen.queryByTestId("dest-harness")).not.toBeInTheDocument();
  });

  it("still renders the shared ORA login page for /login?next=/dashboard", async () => {
    renderLoginRoute("/login?next=%2Fdashboard");
    await waitFor(() => expect(screen.getByTestId("login-card")).toBeInTheDocument());
    expect(screen.queryByTestId("dest-harness")).not.toBeInTheDocument();
  });
});
