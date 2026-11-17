/**
 * MarketingNav.returnTo_regression.test.jsx
 *
 * 2026-11-17 — real bug, proven by a screen recording: a visitor who
 * clicked the top-nav "Sign in" from /harness (not the page's own
 * download-gate button) landed in the ORA app after signing in,
 * instead of back on /harness. Root cause: this shared nav's own
 * Sign in / Start free links were hardcoded to plain /login and
 * /signup with no `?next=` at all — unrelated to the download gate's
 * own (already-correct) redirect.
 *
 * Fix: an optional `returnTo` prop appends `?next=` to both links
 * when set; omitted (every other page using this nav) keeps today's
 * exact hrefs unchanged.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

vi.mock("../../../lib/api", () => ({
  getToken: vi.fn(() => null),
  getUser: vi.fn(() => null),
  isAdminOrFounder: vi.fn(() => false),
}));

import MarketingNav from "../MarketingNav.jsx";

function renderNav(props) {
  return render(
    <MemoryRouter>
      <MarketingNav {...props} />
    </MemoryRouter>,
  );
}

describe("MarketingNav — returnTo prop", () => {
  it("defaults to plain /login and /signup when returnTo is not passed (every page but /harness)", () => {
    renderNav({});
    expect(screen.getByTestId("nav-login")).toHaveAttribute("href", "/login");
    expect(screen.getByTestId("nav-signup-cta")).toHaveAttribute("href", "/signup");
  });

  it("appends ?next=<returnTo> to both Sign in and Start free when passed", () => {
    renderNav({ returnTo: "/harness" });
    expect(screen.getByTestId("nav-login")).toHaveAttribute("href", "/login?next=%2Fharness");
    expect(screen.getByTestId("nav-signup-cta")).toHaveAttribute("href", "/signup?next=%2Fharness");
  });

  it("minimal mode still carries returnTo through", () => {
    renderNav({ minimal: true, logoText: "Aurem Loop", logoSubtext: "Harness", returnTo: "/harness" });
    expect(screen.getByTestId("nav-login")).toHaveAttribute("href", "/login?next=%2Fharness");
  });
});
