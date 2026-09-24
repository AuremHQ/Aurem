/**
 * useGitHubConnectStatus.freshIsolation.test.js — 2026-09-09
 *
 * Founder-reported (screen recording): a BRAND NEW project's connect
 * wizard showed/auto-picked repos from OLD, unrelated GitHub App
 * installations (e.g. `polaris.butler.org` / `AuronHQ`, installed for
 * a completely different earlier project on this same account) — and
 * even looped back to GitHub's own install screen. Root cause:
 * `status.installations` is authoritative but GLOBAL — every
 * installation this GitHub account has EVER granted, forever.
 *
 * `freshInstallIds` narrows that to ONLY installation(s) actually
 * confirmed during THIS hook instance's own startConnect() session,
 * via two independent paths:
 *   1. Precise — postMessage/non-popup-fallback hands us the exact
 *      installation_id GitHub just granted (claimInstallation).
 *   2. Fallback — the poll-success branch diffs current installation
 *      ids against a snapshot taken right before startConnect() was
 *      called, so even a dropped postMessage still gets the right id
 *      (or, for the "no new id" re-configure-existing-org case, all
 *      currently-active ids as a safe degraded fallback).
 *
 * Before this fix, a fresh mount with pre-existing installations
 * already active would immediately expose them as "connected" with
 * no distinction from history — that's exactly what this test file
 * locks out.
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

const STALE_INSTALL = {
  installation_id: 1,
  github_login: "polaris-butler-org",
  repositories: [{ full_name: "polaris-butler-org/old-repo", default_branch: "main" }],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  api.post.mockResolvedValue({ data: { ok: true } });
});
afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

describe("2026-09-09 — full isolation: freshInstallIds", () => {
  it("t_fresh_mount_with_only_stale_history_reports_no_fresh_ids", async () => {
    // Simulates the exact founder repro: account already has an old
    // installation (from a different past project) active BEFORE
    // this wizard even mounts.
    api.get.mockResolvedValue({
      data: {
        installation_active: true,
        installations: [STALE_INSTALL],
        connected_repo: "polaris-butler-org/old-repo",
        repos: [STALE_INSTALL.repositories[0]],
        state: "connected",
      },
    });

    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    // The old, unrelated installation IS in the global status (as
    // ever)...
    expect(result.current.status.installations).toHaveLength(1);
    // ...but a fresh mount must NOT consider it fresh — a new
    // project's wizard filtering on this must show nothing until the
    // user actually connects in this session.
    expect(result.current.freshInstallIds).toEqual([]);
  });

  it("t_postmessage_success_marks_exact_installation_fresh", async () => {
    const popup = { closed: false, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValue(popup);
    api.get.mockResolvedValue({
      data: { installation_active: false, installations: [], state: "pending" },
    });
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); });

    act(() => { result.current.startConnect(); });
    // The authenticated status must confirm this user's installation;
    // a browser-supplied installation ID alone is not authorization.
    api.get.mockResolvedValue({ data: {
      installation_active: true, state: "connected",
      installations: [{ installation_id: 9001, repositories: [{ full_name: "new/repo" }] }],
    } });

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

    expect(result.current.freshInstallIds).toEqual(["9001"]);
  });

  it("t_poll_diff_marks_brand_new_installation_fresh_even_with_stale_history", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fakePopup = { close: vi.fn(), closed: false };
    vi.stubGlobal("open", vi.fn(() => fakePopup));
    // Pre-existing stale installation already active at mount time.
    api.get.mockResolvedValueOnce({
      data: {
        installation_active: true,
        installations: [STALE_INSTALL],
        state: "connected",
      },
    });
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(result.current.freshInstallIds).toEqual([]);

    // User clicks connect for a genuinely NEW org. Snapshot is taken
    // from the CURRENT status (containing only the stale id).
    act(() => { result.current.startConnect(); });

    // Poll tick resolves with the stale install PLUS a brand new one.
    api.get.mockResolvedValue({
      data: {
        installation_active: true,
        installations: [
          STALE_INSTALL,
          { installation_id: 2, github_login: "new-org", repositories: [{ full_name: "new-org/repo" }] },
        ],
        state: "connected",
      },
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });

    // Only the brand-new id (2) is fresh — the stale one (1) is not
    // resurrected just because the user connected something else.
    expect(result.current.freshInstallIds).toEqual(["2"]);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("t_reconfiguring_one_existing_org_does_not_leak_other_unrelated_orgs", async () => {
    // 2026-09-09-b — 2nd founder recording, still happening "after
    // the first step": account has TWO pre-existing installations
    // (e.g. `auravw` and `VeroCapper`, both from past unrelated
    // projects). User re-configures ONLY `auravw` in GitHub's own
    // "Configure" screen (adds a repo to it — no NEW installation_id
    // appears, since the org itself was already installed). Only
    // `auravw` must become fresh; `VeroCapper` must NOT leak in just
    // because SOME connect session happened.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fakePopup = { close: vi.fn(), closed: false };
    vi.stubGlobal("open", vi.fn(() => fakePopup));

    const auravwBefore = {
      installation_id: 10, github_login: "auravw",
      repositories: [{ full_name: "auravw/one" }],
    };
    const veroCapperUntouched = {
      installation_id: 20, github_login: "VeroCapper",
      repositories: [{ full_name: "VeroCapper/old" }],
    };
    api.get.mockResolvedValueOnce({
      data: {
        installation_active: true,
        installations: [auravwBefore, veroCapperUntouched],
        state: "connected",
      },
    });
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(result.current.freshInstallIds).toEqual([]);

    act(() => { result.current.startConnect(); });

    // Poll resolves: auravw's repo grant GREW (same id, more repos —
    // the "Configure existing org" case); VeroCapper is byte-for-byte
    // unchanged (untouched this session).
    const auravwAfter = {
      installation_id: 10, github_login: "auravw",
      repositories: [{ full_name: "auravw/one" }, { full_name: "auravw/two" }],
    };
    api.get.mockResolvedValue({
      data: {
        installation_active: true,
        installations: [auravwAfter, veroCapperUntouched],
        state: "connected",
      },
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });

    expect(result.current.freshInstallIds).toEqual(["10"]);
    expect(result.current.freshInstallIds).not.toContain("20");
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("t_popup_not_prematurely_closed_when_only_stale_data_is_active", async () => {
    // 2026-09-09-c — CRITICAL fix, 3rd founder recording (real prod
    // repro, video evidence): user with a pre-existing installation
    // clicks "Continue with GitHub App" to connect a DIFFERENT/new
    // org via GitHub's own "Sign in with Google" sub-flow. Because
    // `installation_active` was already true from the OLD install,
    // the very FIRST poll tick (2.5s in) used to stopPolling() +
    // popupRef.close() + declare success — killing the user's
    // in-progress popup while they were still mid-authentication on
    // GitHub/Google's own pages (recording: popup dies at
    // "about:blank" right after picking a Google account). Fixed:
    // must NOT close the popup or declare connecting=false/success
    // while the poll is only ever seeing the SAME pre-existing,
    // unchanged installation — has to actually wait for something
    // new/changed.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fakePopup = { close: vi.fn(), closed: false };
    vi.stubGlobal("open", vi.fn(() => fakePopup));

    // Mount: account already has 1 pre-existing installation (from a
    // totally different past project/session).
    api.get.mockResolvedValueOnce({
      data: { installation_active: true, installations: [STALE_INSTALL], state: "connected" },
    });
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    act(() => { result.current.startConnect(); });
    expect(result.current.connecting).toBe(true);

    // Poll ticks 1-3 (7.5s of wall time) all return the EXACT SAME
    // stale installation — nothing has happened yet on GitHub's side,
    // the user is still in the middle of their Google sign-in
    // sub-flow. Every one of these MUST leave the popup alone.
    api.get.mockResolvedValue({
      data: { installation_active: true, installations: [STALE_INSTALL], state: "connected" },
    });
    for (let i = 0; i < 3; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
      expect(fakePopup.close).not.toHaveBeenCalled();
      expect(result.current.connecting).toBe(true);
      expect(result.current.freshInstallIds).toEqual([]);
    }

    // NOW the user actually finishes — a genuinely new org appears.
    api.get.mockResolvedValue({
      data: {
        installation_active: true,
        installations: [STALE_INSTALL, { installation_id: 99, github_login: "brand-new-org", repositories: [{ full_name: "brand-new-org/repo" }] }],
        state: "connected",
      },
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });

    expect(fakePopup.close).toHaveBeenCalled();
    expect(result.current.connecting).toBe(false);
    expect(result.current.freshInstallIds).toEqual(["99"]);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });
});
