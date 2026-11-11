/**
 * Login.Signup.harnessBranding.test.jsx — Fix 8 (2026-11-11) regression.
 *
 * A visitor arriving at /login or /signup with `?next=/harness` came
 * from Aurem Loop's download button, not ORA — the whole page must
 * re-brand itself "Aurem Loop" (header logo, h1, guidelines block,
 * footer legal line) instead of "ORA by Aurem", so it doesn't look
 * like a wrong-product redirect. No `?next=/harness` (i.e. arriving
 * from ORA itself) must keep the exact original "ORA by Aurem"
 * branding with NO guidelines block.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(() => Promise.reject(new Error("no robot-guide in test"))), post: vi.fn() },
  setToken: vi.fn(),
  setUser: vi.fn(),
}));

import Login from "../Login";
import Signup from "../Signup";

function renderAt(path, Page) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<Page />} />
        <Route path="/signup" element={<Page />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom doesn't implement matchMedia — AuthShell's background-image
  // picker (useResponsiveBg) calls it on mount.
  window.matchMedia = window.matchMedia || function matchMedia(query) {
    return { matches: false, media: query, addListener: () => {}, removeListener: () => {} };
  };
});

describe("Login — Fix 8 harness branding", () => {
  it("brands as ORA by Aurem with no guidelines block when there is no ?next=/harness", () => {
    renderAt("/login", Login);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("ORA by Aurem");
    expect(screen.queryByTestId("login-harness-guidelines")).not.toBeInTheDocument();
    expect(screen.getByTestId("login-legal-line")).toHaveTextContent("ORA by Aurem");
  });

  it("brands as Aurem Loop with a guidelines block when ?next=/harness", () => {
    renderAt("/login?next=%2Fharness", Login);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("Aurem Loop");
    expect(screen.queryByTestId("auth-logo")).not.toHaveTextContent("ORA by Aurem");
    expect(screen.getByTestId("login-harness-guidelines")).toBeInTheDocument();
    expect(screen.getByTestId("login-legal-line")).toHaveTextContent("Aurem Loop");
    expect(screen.getByTestId("login-legal-line")).not.toHaveTextContent("ORA by Aurem");
  });
});

describe("Signup — Fix 8 harness branding", () => {
  it("brands as ORA by Aurem with no guidelines block when there is no ?next=/harness", () => {
    renderAt("/signup", Signup);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("ORA by Aurem");
    expect(screen.queryByTestId("signup-harness-guidelines")).not.toBeInTheDocument();
    expect(screen.getByTestId("signup-legal-line")).toHaveTextContent("ORA by Aurem");
  });

  it("brands as Aurem Loop with a guidelines block when ?next=/harness", () => {
    renderAt("/signup?next=%2Fharness", Signup);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("Aurem Loop");
    expect(screen.queryByTestId("auth-logo")).not.toHaveTextContent("ORA by Aurem");
    expect(screen.getByTestId("signup-harness-guidelines")).toBeInTheDocument();
    expect(screen.getByTestId("signup-legal-line")).toHaveTextContent("Aurem Loop");
    expect(screen.getByTestId("signup-legal-line")).not.toHaveTextContent("ORA by Aurem");
  });
});
