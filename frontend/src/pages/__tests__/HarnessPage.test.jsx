/**
 * HarnessPage.test.jsx — Aurem Loop Midnight Gold homepage (2026-11-17).
 *
 * Replaces the prior content-doc-driven Phase A/B test suite — this
 * page is now fully self-contained (hero + inline sign-up/sign-in
 * card + features/FAQ/legal/footer), with only two real backend
 * reads (page-status, release-status) and real auth calls (no
 * simulation): /auth/signup, /auth/login, /auth/login/2fa-verify,
 * /auth/forgot-password, plus the authenticated download endpoint.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  healthApi: { get: vi.fn() },
  api: { get: vi.fn(), post: vi.fn() },
  getToken: vi.fn(() => null),
  setToken: vi.fn(),
  setUser: vi.fn(),
}));

import HarnessPage from "../HarnessPage.jsx";
import { api, healthApi, getToken, setUser } from "../../lib/api";

const RELEASE = {
  available: true,
  version: "0.8.0",
  platform: "windows",
  sha256: "998abc0d46d9acc47de186519d211bf58fff9890ebed85476b58ce724d914543",
  size_bytes: 332855,
};

function mockEndpoints({ flag = true, release = { available: false } } = {}) {
  healthApi.get.mockImplementation((path) => {
    if (path === "/harness/page-status") return Promise.resolve({ data: { harness_page_enabled: flag } });
    if (path === "/harness/release-status") return Promise.resolve({ data: release });
    return Promise.reject(new Error(`unexpected healthApi path ${path}`));
  });
}

function renderPage(entry = "/harness") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes><Route path="/harness" element={<HarnessPage />} /></Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getToken.mockReturnValue(null);
  window.matchMedia = window.matchMedia || function matchMedia(query) {
    return { matches: false, media: query, addListener: () => {}, removeListener: () => {} };
  };
});

describe("HarnessPage — visibility + hero", () => {
  it("shows nothing but the flag check while page-status is pending", () => {
    healthApi.get.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId("harness-page-loading")).toBeInTheDocument();
  });

  it("shows 'not available yet' when the flag is off", async () => {
    mockEndpoints({ flag: false });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page-not-available")).toBeInTheDocument());
    expect(screen.queryByTestId("harness-page")).not.toBeInTheDocument();
  });

  it("renders the real hero, bench box, and sign-up form by default", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(screen.getByText("YOUR MODEL.")).toBeInTheDocument();
    expect(screen.getByTestId("harness-bench")).toHaveTextContent("46/48");
    expect(screen.getByTestId("harness-bench")).toHaveTextContent("31/48");
    expect(screen.getByTestId("harness-bench")).toHaveTextContent("0/48");
    expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument();
  });

  it("shows the real version from release-status, never hardcoded, when a release is published", async () => {
    mockEndpoints({ flag: true, release: RELEASE });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-release-line")).toHaveTextContent("v0.8.0"));
  });

  it("shows a 'not available yet' release line when no release is published", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-release-line")).toHaveTextContent("Download not available yet"));
  });
});

describe("HarnessPage — real signup (source=harness_homepage, no simulation)", () => {
  async function fillSignup({ name = "Ada Lovelace", email = "ada@example.com", password = "SuperStrongPass1!" } = {}) {
    fireEvent.change(screen.getByTestId("harness-signup-name"), { target: { value: name } });
    fireEvent.change(screen.getByTestId("harness-signup-email"), { target: { value: email } });
    fireEvent.change(screen.getByTestId("harness-signup-password"), { target: { value: password } });
    fireEvent.click(screen.getByTestId("harness-signup-submit"));
  }

  it("tags the real signup request with source=harness_homepage", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    api.post.mockResolvedValue({ data: { token: "jwt", user_id: "u1", email: "ada@example.com", name: "Ada Lovelace", tier: "free" } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    await fillSignup();
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/auth/signup",
      expect.objectContaining({ email: "ada@example.com", source: "harness_homepage" }),
    ));
  });

  it("on success, stays on /harness and shows the real congratulations done-view (no navigation away)", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    api.post.mockResolvedValue({ data: { token: "jwt", user_id: "u1", email: "ada@example.com", name: "Ada Lovelace", tier: "free" } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    await fillSignup();
    await waitFor(() => expect(screen.getByTestId("harness-done-view")).toBeInTheDocument());
    expect(screen.getByTestId("harness-done-title")).toHaveTextContent("Congratulations, Ada");
  });

  it("on a real backend error (e.g. duplicate email), shows the REAL error — never a fake success", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    api.post.mockRejectedValue({ response: { data: { detail: "Email already registered" } } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    await fillSignup();
    await waitFor(() => expect(screen.getByTestId("harness-form-msg")).toHaveTextContent("Email already registered"));
    expect(screen.queryByTestId("harness-done-view")).not.toBeInTheDocument();
  });

  it("blocks submit client-side on an invalid email or weak password, with no API call", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    await fillSignup({ email: "not-an-email", password: "weak" });
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByTestId("harness-form-msg")).toHaveTextContent("Please fix the highlighted fields");
  });
});

describe("HarnessPage — real signin", () => {
  it("signs in with real credentials and shows the done-view, staying on /harness", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    api.post.mockResolvedValue({ data: { token: "jwt", user_id: "u1", email: "ada@example.com", name: "Ada Lovelace", tier: "free" } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-tab-signin")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-tab-signin"));
    fireEvent.change(screen.getByTestId("harness-signin-email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByTestId("harness-signin-password"), { target: { value: "whatever" } });
    fireEvent.click(screen.getByTestId("harness-signin-submit"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/auth/login", { email: "ada@example.com", password: "whatever" }));
    await waitFor(() => expect(screen.getByTestId("harness-done-view")).toBeInTheDocument());
    expect(screen.getByTestId("harness-done-title")).toHaveTextContent("Welcome back, Ada");
  });

  it("routes to the real inline 2FA step when the backend requires it, never fakes success", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    api.post.mockResolvedValue({ data: { mfa_required: true, mfa_token: "mfa_tok", email: "ada@example.com" } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-tab-signin")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-tab-signin"));
    fireEvent.change(screen.getByTestId("harness-signin-email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByTestId("harness-signin-password"), { target: { value: "whatever" } });
    fireEvent.click(screen.getByTestId("harness-signin-submit"));
    await waitFor(() => expect(screen.getByTestId("harness-mfa-code")).toBeInTheDocument());
    expect(screen.queryByTestId("harness-done-view")).not.toBeInTheDocument();

    api.post.mockResolvedValue({ data: { token: "jwt", user_id: "u1", email: "ada@example.com", name: "Ada Lovelace" } });
    fireEvent.change(screen.getByTestId("harness-mfa-code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByTestId("harness-mfa-submit"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/auth/login/2fa-verify", { mfa_token: "mfa_tok", code: "123456" }));
    await waitFor(() => expect(screen.getByTestId("harness-done-view")).toBeInTheDocument());
  });

  it("shows the real sign-in error on bad credentials", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    api.post.mockRejectedValue({ response: { data: { detail: "Invalid email or password" } } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-tab-signin")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-tab-signin"));
    fireEvent.change(screen.getByTestId("harness-signin-email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByTestId("harness-signin-password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByTestId("harness-signin-submit"));
    await waitFor(() => expect(screen.getByTestId("harness-form-msg")).toHaveTextContent("Invalid email or password"));
  });

  it("Google and GitHub buttons redirect with next=/harness, same-page return", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-tab-signin")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-tab-signin"));
    const originalLocation = window.location;
    delete window.location;
    window.location = { href: "", origin: "http://localhost:3000" };
    try {
      fireEvent.click(screen.getByTestId("harness-oauth-google"));
      expect(window.location.href).toBe(
        "http://localhost:3000/api/aurem-dev/google/oauth/start?intent=signup&next=%2Fharness",
    );
    // GitHub goes straight to github.com on the first click, same as
    // Google above — no awaited funnel step in between, and the return
    // is the ?next= destination, never /dashboard.
    window.location.href = "";
    fireEvent.click(screen.getByTestId("harness-oauth-github"));
    expect(window.location.href).toBe(
      "http://localhost:3000/api/aurem-dev/github/oauth/connect?signup=1&intent=signup&next=%2Fharness",
    );
  } finally {
      window.location = originalLocation;
    }
  });
});

describe("HarnessPage — real forgot-password (no fake confirmation)", () => {
  it("sends a real reset-link request and shows the real response message", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    api.post.mockResolvedValue({ data: { ok: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-tab-signin")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-tab-signin"));
    fireEvent.click(screen.getByTestId("harness-forgot-btn"));
    fireEvent.change(screen.getByTestId("harness-reset-email"), { target: { value: "ada@example.com" } });
    fireEvent.click(screen.getByTestId("harness-reset-submit"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/auth/forgot-password", { email: "ada@example.com" }));
    await waitFor(() => expect(screen.getByTestId("harness-form-msg")).toHaveTextContent("reset link has been sent"));
  });
});

describe("HarnessPage — already signed in on page load", () => {
  it("skips the form and shows the done-view directly when a valid token already exists", async () => {
    getToken.mockReturnValue("existing-jwt");
    mockEndpoints({ flag: true, release: { available: false } });
    api.get.mockResolvedValue({ data: { user: { name: "Returning User" } } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-done-view")).toBeInTheDocument());
    expect(screen.getByTestId("harness-done-title")).toHaveTextContent("Welcome back, Returning");
    expect(screen.queryByTestId("harness-signup-submit")).not.toBeInTheDocument();
  });

  it("falls back to the sign-up form when the stored token is no longer valid", async () => {
    getToken.mockReturnValue("expired-jwt");
    mockEndpoints({ flag: true, release: { available: false } });
    api.get.mockRejectedValue({ response: { status: 401 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
  });
});

describe("HarnessPage — real authenticated download + congratulations", () => {
  async function signUpAndReachDone() {
    api.post.mockResolvedValueOnce({ data: { token: "jwt", user_id: "u1", email: "ada@example.com", name: "Ada Lovelace" } });
    fireEvent.change(screen.getByTestId("harness-signup-name"), { target: { value: "Ada Lovelace" } });
    fireEvent.change(screen.getByTestId("harness-signup-email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByTestId("harness-signup-password"), { target: { value: "SuperStrongPass1!" } });
    fireEvent.click(screen.getByTestId("harness-signup-submit"));
    await waitFor(() => expect(screen.getByTestId("harness-done-view")).toBeInTheDocument());
  }

  it("downloads the real authenticated package and shows the congratulations box", async () => {
    mockEndpoints({ flag: true, release: RELEASE });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    await signUpAndReachDone();

    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    global.URL.createObjectURL = createObjectURL;
    global.URL.revokeObjectURL = revokeObjectURL;
    api.post.mockResolvedValueOnce({
      data: new Blob(["zip-bytes"]),
      headers: { "x-aurem-package-sha256": RELEASE.sha256 },
    });
    fireEvent.click(screen.getByTestId("harness-download-btn"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/harness/download", {}, { responseType: "blob" }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob)));
    await waitFor(() => expect(screen.getByTestId("harness-download-success")).toHaveTextContent(
      "🎊 Congratulations! Successfully downloaded",
    ));
  });

  it("shows the real error on a download failure, never a fake success", async () => {
    mockEndpoints({ flag: true, release: RELEASE });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    await signUpAndReachDone();

    api.post.mockRejectedValueOnce({ response: { status: 409 } });
    fireEvent.click(screen.getByTestId("harness-download-btn"));
    await waitFor(() => expect(screen.getByTestId("harness-download-error")).toHaveTextContent("No release is published yet"));
    expect(screen.queryByTestId("harness-download-success")).not.toBeInTheDocument();
  });
  it("shows the .exe as the primary download and the .zip as an Advanced link when an installer is attached (PR #342)", async () => {
    const EXE_RELEASE = { ...RELEASE, exe_available: true, exe_sha256: "aabbccdd".repeat(8), exe_size_bytes: 123456 };
    mockEndpoints({ flag: true, release: EXE_RELEASE });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    await signUpAndReachDone();

    expect(screen.getByTestId("harness-download-exe-btn")).toHaveTextContent("Download for Windows (.exe) — v0.8.0");
    expect(screen.getByTestId("harness-download-zip-link")).toHaveTextContent("Advanced: download the .zip instead");
    expect(screen.queryByTestId("harness-download-btn")).not.toBeInTheDocument();
    expect(document.body.textContent).toContain("aabbccdd");

    global.URL.createObjectURL = vi.fn(() => "blob:mock-url");
    global.URL.revokeObjectURL = vi.fn();
    api.post.mockResolvedValueOnce({ data: new Blob(["exe-bytes"]), headers: {} });
    fireEvent.click(screen.getByTestId("harness-download-exe-btn"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/harness/download-exe", {}, { responseType: "blob" }));
    await waitFor(() => expect(screen.getByTestId("harness-download-success")).toHaveTextContent("🎊 Congratulations! Successfully downloaded"));
  });

  it("keeps the .zip as the primary download when no installer is attached", async () => {
    mockEndpoints({ flag: true, release: RELEASE });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    await signUpAndReachDone();

    expect(screen.getByTestId("harness-download-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-download-exe-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("harness-download-zip-link")).not.toBeInTheDocument();
  });

});

describe("HarnessPage — harness auth fix (2026-11-20)", () => {
  it("Sign-up tab offers the same Google and GitHub buttons as the Sign-in tab", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    expect(screen.getByTestId("harness-oauth-google")).toBeInTheDocument();
    expect(screen.getByTestId("harness-oauth-github")).toBeInTheDocument();
    const originalLocation = window.location;
    delete window.location;
    window.location = { href: "", origin: "http://localhost:3000" };
    try {
      fireEvent.click(screen.getByTestId("harness-oauth-github"));
      expect(window.location.href).toBe(
        "http://localhost:3000/api/aurem-dev/github/oauth/connect?signup=1&intent=signup&next=%2Fharness",
      );
    } finally {
      window.location = originalLocation;
    }
  });

  it("opens on the Sign-in tab when ?tab=signin is in the URL and stays there", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage("/harness?tab=signin");
    await waitFor(() => expect(screen.getByTestId("harness-signin-submit")).toBeInTheDocument());
    expect(screen.queryByTestId("harness-signup-submit")).not.toBeInTheDocument();
  });

  it("refreshes the cached user from /auth/me so the shown account is the signed-in account", async () => {
    getToken.mockReturnValue("tejinder-jwt");
    mockEndpoints({ flag: true, release: { available: false } });
    api.get.mockResolvedValue({
      data: {
        user: {
          user_id: "u-tejinder",
          email: "tejinder@example.com",
          name: "Tejinder",
          tier: "free",
          tokens_remaining: 10,
          created_at: "2026-01-01T00:00:00.000Z",
        },
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-done-view")).toBeInTheDocument());
    expect(setUser).toHaveBeenCalledWith({
      user_id: "u-tejinder",
      email: "tejinder@example.com",
      name: "Tejinder",
      tier: "free",
      tokens_remaining: 10,
      created_at: "2026-01-01T00:00:00.000Z",
    });
  });

  it("clears the cached user when /auth/me fails so a previous account is never shown", async () => {
    getToken.mockReturnValue("expired-jwt");
    mockEndpoints({ flag: true, release: { available: false } });
    api.get.mockRejectedValue({ response: { status: 401 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-signup-submit")).toBeInTheDocument());
    expect(setUser).toHaveBeenCalledWith(null);
  });

  it("shows the sign-in form on the first click even when the server logout fails", async () => {
    getToken.mockReturnValue("expired-jwt");
    mockEndpoints({ flag: true, release: { available: false } });
    api.get.mockResolvedValue({ data: { user: { name: "Returning User" } } });
    api.post.mockRejectedValue({ response: { status: 401 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-done-view")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-signout-again"));
    await waitFor(() => expect(screen.getByTestId("harness-signin-submit")).toBeInTheDocument());
    expect(screen.queryByTestId("harness-signout-again")).not.toBeInTheDocument();
  });
});

describe("HarnessPage — sign out and show the form again", () => {
  it("fires a real logout call, clears local session, and returns to the sign-in form", async () => {
    getToken.mockReturnValue("existing-jwt");
    mockEndpoints({ flag: true, release: { available: false } });
    api.get.mockResolvedValue({ data: { user: { name: "Returning User" } } });
    api.post.mockResolvedValue({ data: { ok: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-done-view")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-signout-again"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/auth/logout", {}, { headers: { Authorization: "Bearer existing-jwt" } }));
    await waitFor(() => expect(screen.getByTestId("harness-signin-submit")).toBeInTheDocument());
  });
});

describe("HarnessPage — FAQ, Licensing & Policies, footer", () => {
  it("expands exactly one FAQ item at a time", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-faq-0")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-faq-0"));
    expect(screen.getByTestId("harness-faq-0")).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByTestId("harness-faq-1"));
    expect(screen.getByTestId("harness-faq-0")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("harness-faq-1")).toHaveAttribute("aria-expanded", "true");
  });

  it("renders all three Licensing & Policies articles (License, Privacy, Terms)", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-legal-license")).toBeInTheDocument());
    expect(screen.getByTestId("harness-legal-license")).toHaveTextContent("Software Licence");
    expect(screen.getByTestId("harness-legal-privacy")).toHaveTextContent("Privacy Policy");
    expect(screen.getByTestId("harness-legal-terms")).toHaveTextContent("Terms of Use");
  });

  it("renders the real footer with the exact copyright and trademark lines", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-footer")).toBeInTheDocument());
    expect(screen.getByTestId("harness-footer")).toHaveTextContent(
      "© 2026 Polaris Built Inc — Aurem Loop, made by AUREM · Incorporated in Canada · All rights reserved.",
    );
    expect(screen.getByTestId("harness-footer")).toHaveTextContent(
      "AUREM™ is a trademark of Polaris Built Inc. (Application No. 2492318, filed with the Canadian Intellectual Property Office).",
    );
  });

  it("'Cookie preferences' fires the real site-wide consent reopen — no separate fake panel", async () => {
    mockEndpoints({ flag: true, release: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-cookie-prefs")).toBeInTheDocument());
    const handler = vi.fn();
    window.addEventListener("aurem:reopen-consent", handler);
    try {
      fireEvent.click(screen.getByTestId("harness-cookie-prefs"));
      expect(handler).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("aurem:reopen-consent", handler);
    }
  });
});
