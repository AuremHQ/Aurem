/**
 * useGitHubConnectStatus.claimFallback.test.js — 2026-09-04
 *
 * Founder-reported repro (screen recording): reconnecting an account
 * that ALREADY has the GitHub App installed loops "Connection didn't
 * finish" / silently reverts to "No repo connected". Root cause
 * (backend, routers/github_app.py): GitHub drops the `state` query
 * param entirely when redirecting through its own "manage
 * installation" screen for an already-installed account, so the
 * `/callback` couldn't always attribute the install to a user.
 *
 * This test covers the CLIENT half of the fix: the authenticated tab
 * that's actually driving the connect flow claims the installation_id
 * directly (via `POST /github/app/claim`) as soon as it learns about
 * it — from the popup's postMessage, or from the non-popup fallback
 * link's landing query params — instead of depending on the fragile
 * GitHub state round-trip at all.
 */
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  API_BASE: "https://api.test",
  getToken: () => "fake-token",
}));
vi.mock("../../lib/githubFunnel", () => ({ trackFunnel: vi.fn() }));

import useGitHubConnectStatus from "../useGitHubConnectStatus.js";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({
    data: { installation_active: false, state: "pending", installations: [] },
  });
  api.post.mockResolvedValue({ data: { ok: true } });
});
afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

describe("2026-09-04 — claim fallback for GitHub's dropped `state`", () => {
  it("t_postMessage_success_claims_installation_before_refreshing_status", async () => {
    const popup = { closed: false, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValue(popup);
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); }); // initial mount fetch
    act(() => { result.current.startConnect(); });

    api.get.mockClear();
    await act(async () => {
      const event = new MessageEvent("message", {
        origin: "https://api.test",
        data: { type: "aurem-app-installed", status: "success", install_id: 9001 },
      });
      Object.defineProperty(event, "source", { value: popup });
      window.dispatchEvent(event);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.post).toHaveBeenCalledWith(
      "/github/app/claim", { installation_id: 9001 },
    );
    expect(api.get).toHaveBeenCalledWith("/github/app/status");
    void result;
  });

  it("t_postMessage_error_does_not_claim_anything", async () => {
    renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "aurem-app-installed", status: "err", err: "invalid_state" },
      }));
      await Promise.resolve();
    });

    expect(api.post).not.toHaveBeenCalled();
  });

  it("t_nonpopup_fallback_landing_claims_and_strips_query_params", async () => {
    window.history.replaceState(
      {}, "",
      "/dashboard?flow=connect-repo&install=success&install_id=7003",
    );

    renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(api.post).toHaveBeenCalledWith(
      "/github/app/claim", { installation_id: 7003 },
    );
    expect(window.location.search).toBe("");
  });

  it("t_nonpopup_fallback_landing_without_success_only_refreshes_no_claim", async () => {
    window.history.replaceState(
      {}, "",
      "/dashboard?flow=connect-repo&err=invalid_state",
    );

    renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(api.post).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
  });

  it("t_no_connect_repo_flow_param_leaves_url_and_claim_untouched", async () => {
    window.history.replaceState({}, "", "/dashboard?other=1");

    renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); });

    expect(api.post).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?other=1");
  });
});
