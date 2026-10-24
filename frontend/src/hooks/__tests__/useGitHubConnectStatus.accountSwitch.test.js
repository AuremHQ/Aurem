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
  api: { get: vi.fn(() => Promise.resolve({ data: { installation_active: false, installations: [] } })) },
  API_BASE: "https://api.test",
  getToken: () => "fake-token",
}));
vi.mock("../../lib/githubFunnel", () => ({ trackFunnel: vi.fn() }));

import useGitHubConnectStatus from "../useGitHubConnectStatus.js";

describe("useGitHubConnectStatus — account-switch-first connect", () => {
  it("startConnect() opens the account-switch endpoint, not /github/app/install directly", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue({ closed: false });
    const { result } = renderHook(() => useGitHubConnectStatus());

    act(() => { result.current.startConnect(); });

    expect(openSpy).toHaveBeenCalledTimes(1);
    const [url] = openSpy.mock.calls[0];
    expect(url).toContain("/github/oauth/connect");
    expect(url).toContain("app_install=1");
    expect(url).not.toContain("/github/app/install?");
    openSpy.mockRestore();
  });

  it("the exposed installUrl fallback link uses the same account-switch endpoint", () => {
    const { result } = renderHook(() => useGitHubConnectStatus());
    expect(result.current.installUrl).toContain("/github/oauth/connect");
    expect(result.current.installUrl).toContain("app_install=1");
  });
});
