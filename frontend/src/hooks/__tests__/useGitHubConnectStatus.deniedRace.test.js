/**
 * useGitHubConnectStatus.deniedRace.test.js — 2026-09-12
 *
 * Founder flagged (independent of the mandatory-gate bug, "worth a
 * quick look"): 3 real users hit `app_install_denied` once or twice
 * before eventually succeeding on a later attempt — possible friction
 * at GitHub's own permission screen.
 *
 * Investigated the success/denial detection logic directly and found
 * a plausible, concrete race: GitHub Apps have no real deny callback
 * — a closed popup with no confirmed grant yet is the ONLY signal
 * used to declare denial. On a genuinely successful grant, GitHub's
 * own flow can auto-close the popup the instant the user finishes,
 * but this app's backend can take slightly longer than one
 * `POLL_INTERVAL_MS` (2.5s) tick to actually persist
 * `installation_active` from the webhook. A poll tick landing in
 * that exact window sees "popup closed" + "not active yet" and fires
 * a FALSE denial for what was really a success.
 *
 * Fix: on a closed popup, spend a short grace window (2 re-checks,
 * 1s apart) re-testing for the grant before ever declaring denial.
 */
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn() },
  API_BASE: "https://api.test",
  getToken: () => "fake-token",
}));
vi.mock("../../lib/githubFunnel", () => ({ trackFunnel: vi.fn() }));

import useGitHubConnectStatus from "../useGitHubConnectStatus.js";
import { api } from "../../lib/api";
import { trackFunnel } from "../../lib/githubFunnel";

function fakePopup() {
  return { closed: false, close: vi.fn() };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});
afterEach(() => { vi.useRealTimers(); });

describe("2026-09-12 — grace window recovers a real grant from a false denial", () => {
  it("t_webhook_catches_up_during_grace_window_recovers_as_granted_not_denied", async () => {
    const popup = fakePopup();
    vi.spyOn(window, "open").mockReturnValue(popup);
    // Initial mount: brand new user, nothing installed yet.
    api.get.mockResolvedValueOnce({
      data: { installation_active: false, state: "pending", installations: [] },
    });
    // First poll tick (2.5s in): popup already closed by GitHub's own
    // auto-close, but our webhook hasn't landed yet — still false.
    api.get.mockResolvedValueOnce({
      data: { installation_active: false, state: "pending", installations: [] },
    });
    // Grace re-check #1 (1s later, 3.5s total): webhook finally lands.
    api.get.mockResolvedValue({
      data: { installation_active: true, state: "pending",
              installations: [{ installation_id: 7, repositories: [{ full_name: "x/y" }] }] },
    });

    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); });

    act(() => { result.current.startConnect(); });
    popup.closed = true;

    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    // Still mid grace-window — must NOT have declared denial yet.
    expect(result.current.denied).toBe(false);
    expect(trackFunnel).not.toHaveBeenCalledWith(
      "app_install_denied", "wizard", expect.anything(),
    );

    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });

    expect(result.current.denied).toBe(false);
    expect(result.current.connecting).toBe(false);
    expect(trackFunnel).toHaveBeenCalledWith("app_install_granted", "wizard");
    expect(trackFunnel).not.toHaveBeenCalledWith(
      "app_install_denied", "wizard", expect.anything(),
    );
  });
});
