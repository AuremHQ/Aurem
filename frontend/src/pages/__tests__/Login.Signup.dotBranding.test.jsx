/**
 * Login.Signup.dotBranding.test.jsx — ORA dot gating, 2026-11-14.
 *
 * Mirrors Login.Signup.harnessBranding.test.jsx (Fix 8): a visitor
 * arriving at /login or /signup with `?next=/dot` came from the ORA
 * dot page, not ORA — the whole page must re-brand itself "ORA dot"
 * (header logo, h1, guidelines block, footer legal line) instead of
 * "ORA by Aurem", so it doesn't look like a wrong-product redirect.
 * No `?next=/dot` (i.e. arriving from ORA itself) must keep the exact
 * original "ORA by Aurem" branding with NO guidelines block.
 *
 * The redirect itself needs no new code: Login.jsx/Signup.jsx already
 * navigate to `next` after email auth, and the GitHub/Google OAuth
 * routers already carry `next` through the callback (Fix 7) — any
 * valid relative path, including /dot. These tests lock in the
 * branding + the cross-links that keep `next` intact.
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

describe("Login — ORA dot branding", () => {
  it("brands as ORA by Aurem with no guidelines block when there is no ?next=/dot", () => {
    renderAt("/login", Login);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("ORA by Aurem");
    expect(screen.queryByTestId("login-dot-guidelines")).not.toBeInTheDocument();
    expect(screen.getByTestId("login-legal-line")).toHaveTextContent("ORA by Aurem");
  });

  it("brands as ORA dot with a guidelines block when ?next=/dot", () => {
    renderAt("/login?next=%2Fdot", Login);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("ORA dot");
    expect(screen.queryByTestId("auth-logo")).not.toHaveTextContent("ORA by Aurem");
    expect(screen.getByTestId("login-dot-guidelines")).toBeInTheDocument();
    expect(screen.getByTestId("login-legal-line")).toHaveTextContent("ORA dot");
    expect(screen.getByTestId("login-legal-line")).not.toHaveTextContent("ORA by Aurem");
  });

  it("does NOT treat a path that merely starts with /dot (e.g. /dotfoo) as the dot flow", () => {
    renderAt("/login?next=%2Fdotfoo", Login);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("ORA by Aurem");
    expect(screen.queryByTestId("login-dot-guidelines")).not.toBeInTheDocument();
  });

  it("carries ?next= through the 'No account yet?' cross-link to /signup", () => {
    renderAt("/login?next=%2Fdot", Login);
    expect(screen.getByTestId("login-to-signup")).toHaveAttribute("href", "/signup?next=%2Fdot");
  });

  it("the robot guide's corner label reads ORA DOT, not ORA, when ?next=/dot", () => {
    renderAt("/login?next=%2Fdot", Login);
    expect(screen.getByTestId("login-robot-guide")).toHaveTextContent("ORA DOT GUIDE");
    expect(screen.getByTestId("login-robot-guide")).not.toHaveTextContent("ORA GUIDE");
  });

  it("shows the ORA dot hero headline on the dot-branded login page", () => {
    renderAt("/login?next=%2Fdot", Login);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Your AI workspace, on your own machine.");
  });

  it("the logo and Home link point to /dot (not ORA's /) when ?next=/dot", () => {
    renderAt("/login?next=%2Fdot", Login);
    expect(screen.getByTestId("auth-logo")).toHaveAttribute("href", "/dot");
    expect(screen.getByTestId("auth-back-home")).toHaveAttribute("href", "/dot");
  });

  it("the logo and Home link still point to / with no ?next=/dot", () => {
    renderAt("/login", Login);
    expect(screen.getByTestId("auth-logo")).toHaveAttribute("href", "/");
    expect(screen.getByTestId("auth-back-home")).toHaveAttribute("href", "/");
  });
});

describe("Signup — ORA dot branding", () => {
  it("brands as ORA by Aurem with no guidelines block when there is no ?next=/dot", () => {
    renderAt("/signup", Signup);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("ORA by Aurem");
    expect(screen.queryByTestId("signup-dot-guidelines")).not.toBeInTheDocument();
    expect(screen.getByTestId("signup-legal-line")).toHaveTextContent("ORA by Aurem");
  });

  it("brands as ORA dot with a guidelines block when ?next=/dot", () => {
    renderAt("/signup?next=%2Fdot", Signup);
    expect(screen.getByTestId("auth-logo")).toHaveTextContent("ORA dot");
    expect(screen.queryByTestId("auth-logo")).not.toHaveTextContent("ORA by Aurem");
    expect(screen.getByTestId("signup-dot-guidelines")).toBeInTheDocument();
    expect(screen.getByTestId("signup-legal-line")).toHaveTextContent("ORA dot");
    expect(screen.getByTestId("signup-legal-line")).not.toHaveTextContent("ORA by Aurem");
  });

  it("carries ?next= through the 'Already have an account?' cross-link to /login", () => {
    renderAt("/signup?next=%2Fdot", Signup);
    expect(screen.getByTestId("signup-to-login")).toHaveAttribute("href", "/login?next=%2Fdot");
  });

  it("the robot guide's corner label reads ORA DOT, not ORA, when ?next=/dot", () => {
    renderAt("/signup?next=%2Fdot", Signup);
    expect(screen.getByTestId("signup-robot-guide")).toHaveTextContent("ORA DOT GUIDE");
    expect(screen.getByTestId("signup-robot-guide")).not.toHaveTextContent("ORA GUIDE");
  });

  it("shows the ORA dot hero headline on the dot-branded signup page", () => {
    renderAt("/signup?next=%2Fdot", Signup);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Your AI workspace, on your own machine.");
  });

  it("the logo and Home link point to /dot (not ORA's /) when ?next=/dot", () => {
    renderAt("/signup?next=%2Fdot", Signup);
    expect(screen.getByTestId("auth-logo")).toHaveAttribute("href", "/dot");
    expect(screen.getByTestId("auth-back-home")).toHaveAttribute("href", "/dot");
  });

  it("the logo and Home link still point to / with no ?next=/dot", () => {
    renderAt("/signup", Signup);
    expect(screen.getByTestId("auth-logo")).toHaveAttribute("href", "/");
    expect(screen.getByTestId("auth-back-home")).toHaveAttribute("href", "/");
  });
});
