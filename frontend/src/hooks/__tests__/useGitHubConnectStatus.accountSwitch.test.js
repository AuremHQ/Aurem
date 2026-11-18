/**
 * useGitHubConnectStatus.accountSwitch.test.js — 2026-10-24
 *
 * Founder-reported bug: "Add repository" for a SECOND project kept
 * landing on the FIRST project's already-installed GitHub account's
 * settings page — standard, unavoidable GitHub behavior for
 * `github.com/apps/<slug>/installations/new` on an already-
 * authenticated browser, with no query param to override it.
 *
 * Fix: `startConnect()` (and the `installUrl` fallback link) now route
 * through `/github/oauth/connect?app_install=1` FIRST — a lightweight
 * identity-only OAuth round-trip that always forces GitHub's
 * `prompt=select_account` account picker — instead of hitting
 * `/github/app/install` directly. See routers/github_oauth.py's
 * `app_install_switch` flow for what happens after the user picks.
 */
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("../../lib/api", () => ({
  api: {
    get: vi.fn(() => Promise.resolve({ data: { installation_active: false, installations: [] } })),
    post: vi.fn(() => Promise.resolve({ data: { link_token: "fake-link-token" } })),
  },
  API_BASE: "https://api.test",
  getToken: () => "fake-token",
}));
vi.mock("../../lib/githubFunnel", () => ({ trackFunnel: vi.fn() }));

import useGitHubConnectStatus from "../useGitHubConnectStatus.js";

describe("useGitHubConnectStatus — account-switch-first connect", () => {
  it("startConnect() opens the account-switch endpoint, not /github/app/install directly", async () => {
    // FIX #1 (2026-11-17) -- the popup is opened blank and navigated with
    // a minted link token once the mint resolves, so the fake popup needs
    // a location to navigate and a close method for the mint-failure path.
    const popup = { closed: false, location: { href: "" }, close: vi.fn() };
    const openSpy = vi.spyOn(window, "open").mockReturnValue(popup);
    const { result } = renderHook(() => useGitHubConnectStatus());

    act(() => { result.current.startConnect(); });

    expect(openSpy).toHaveBeenCalledTimes(1);
    await act(async () => {});
    expect(popup.location.href).toContain("/github/oauth/connect");
    expect(popup.location.href).toContain("app_install=1");
    expect(popup.location.href).not.toContain("/github/app/install?");
    expect(popup.location.href).toContain("fake-link-token");
    expect(popup.location.href).not.toContain("fake-token");
    openSpy.mockRestore();
  });

  it("the exposed installUrl fallback link uses the same account-switch endpoint", async () => {
    const { result } = renderHook(() => useGitHubConnectStatus());
    // FIX #1 (2026-11-17) -- installUrl is minted asynchronously from a
    // link token on mount; wait for the mint before asserting.
    await act(async () => {});
    expect(result.current.installUrl).toContain("/github/oauth/connect");
    expect(result.current.installUrl).toContain("app_install=1");
    expect(result.current.installUrl).toContain("fake-link-token");
    expect(result.current.installUrl).not.toContain("fake-token");
  });
});
