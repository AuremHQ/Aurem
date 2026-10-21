/**
 * CookieConsentBanner.loginTransition.test.jsx — UI-fix C9 (2026-10)
 *
 * Audit finding: the banner disappears between /signup and /dashboard
 * "without a click". Confirmed INTENTIONAL (see the comment above the
 * `loggedIn` state in CookieConsentBanner.jsx) — not changed here,
 * since making it wait for an explicit choice would reopen the
 * ORA GUIDE z-index collision it was built to prevent. This locks in
 * the current, deliberate behavior as a regression guard, and
 * confirms the normal (logged-out, user clicks) path still persists
 * the choice to localStorage as designed.
 */
import React from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import CookieConsentBanner from "../CookieConsentBanner";
import { STORAGE_KEY } from "../../lib/cookieConsentStorage";

function Harness() {
  const navigate = useNavigate();
  return (
    <>
      <button data-testid="go-dashboard" onClick={() => navigate("/dashboard")}>go</button>
      <CookieConsentBanner />
    </>
  );
}

beforeEach(() => {
  localStorage.clear();
  delete window.__gpc;
});

describe("UI-fix C9 · cookie banner vs. login transition", () => {
  it("shows for a logged-out visitor on /signup", () => {
    render(<MemoryRouter initialEntries={["/signup"]}><CookieConsentBanner /></MemoryRouter>);
    expect(screen.getByTestId("cookie-consent-banner")).toBeInTheDocument();
  });

  it("(intended) hides once aurem_token is set, even with no explicit choice yet — no consent record is written", () => {
    render(<MemoryRouter initialEntries={["/signup"]}><Harness /></MemoryRouter>);
    expect(screen.getByTestId("cookie-consent-banner")).toBeInTheDocument();

    // Simulate a successful signup: token written, app navigates to /dashboard.
    localStorage.setItem("aurem_token", "tok123");
    fireEvent.click(screen.getByTestId("go-dashboard"));

    expect(screen.queryByTestId("cookie-consent-banner")).not.toBeInTheDocument();
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("clicking Accept all still persists the real choice to localStorage", () => {
    render(<MemoryRouter initialEntries={["/signup"]}><CookieConsentBanner /></MemoryRouter>);
    fireEvent.click(screen.getByTestId("cookie-accept-btn"));
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    expect(saved.choice).toBe("all");
    expect(screen.queryByTestId("cookie-consent-banner")).not.toBeInTheDocument();
  });
});
