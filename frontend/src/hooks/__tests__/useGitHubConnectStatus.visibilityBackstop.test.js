/**
 * useGitHubConnectStatus.visibilityBackstop.test.js
 *
 * Real-user repro (screen recording, 2026-10-05): GitHub App already
 * installed -> GitHub routes the popup through its OWN "manage
 * installation" screen instead of a fresh consent flow -> user
 * changes the repo selection and clicks Save -> GitHub shows its own
 * "Okay, AUREM DevOps was updated" confirmation page. That page never
 * messages window.opener and never redirects back (it's GitHub's UI,
 * not our /callback) and does not auto-close.
 *
 * Grant detection does not actually depend on that message — it
 * diffs the polled /status repo signature against a baseline — so
 * this should self-heal on the next poll tick regardless. The real
 * gap: the AUREM tab is backgrounded (hidden) for the entire time the
 * user is in the GitHub popup, and browsers throttle `setInterval`
 * hard on hidden tabs, so the poll that would normally catch the
 * change within POLL_INTERVAL_MS doesn't fire anywhere near on time —
 * it just reads as permanently stuck on "Waiting for you to finish in
 * the GitHub popup...".
 *
 * Fix: a `visibilitychange` listener forces one unthrottled
 * /status check (and the matching grant/sync check) the instant the
 * user comes back to this tab, instead of waiting on the throttled
 * timer. This mirrors the backstop NewUserWizard.jsx's separate
 * OAuth-login flow already has for the identical browser behavior.
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

function fakePopup() {
  return { closed: false, close: vi.fn() };
}

function setHidden(hidden) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true, get: () => (hidden ? "hidden" : "visible"),
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, "visibilityState", {
    configurable: true, get: () => "visible",
  });
});

describe("visibilitychange backstop — background-tab throttling", () => {
  it("t_tab_refocus_immediately_detects_repo_selection_change_without_waiting_for_throttled_poll", async () => {
    const popup = fakePopup();
    vi.spyOn(window, "open").mockReturnValue(popup);

    // Pre-existing installation (this is the "already installed,
    // repo selection changed via GitHub's own manage-install screen"
    // case, not a fresh install) — baseline repo signature is set.
    api.get.mockResolvedValueOnce({
      data: {
        installation_active: true, state: "connected",
        installations: [{ installation_id: 42, repositories: [{ full_name: "org/old-repo" }] }],
      },
    });

    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); }); // initial mount fetch (baseline)

    // User clicks "Continue with GitHub App"; GitHub's own manage-
    // installation screen backgrounds this tab for the rest of the
    // flow (popup never closes — it just sits on GitHub's own
    // confirmation page, which never messages or redirects back).
    act(() => { result.current.startConnect(); });
    setHidden(true);

    // GitHub-side change has already happened (repo selection
    // updated), but the regular poll tick never gets a chance to run
    // at full speed because the tab is hidden — simulate the browser
    // throttling it by simply not advancing the fake timer at all.
    api.get.mockResolvedValue({
      data: {
        installation_active: true, state: "connected",
        installations: [{ installation_id: 42, repositories: [{ full_name: "org/new-repo" }] }],
      },
    });

    expect(result.current.connecting).toBe(true);

    // User comes back to the AUREM tab — this must detect the grant
    // immediately, without first needing a throttled interval tick.
    await act(async () => {
      setHidden(false);
      await Promise.resolve();
    });

    expect(result.current.connecting).toBe(false);
    expect(result.current.denied).toBe(false);
  });

  it("t_visibility_backstop_is_a_noop_when_nothing_is_in_flight", async () => {
    api.get.mockResolvedValue({
      data: { installation_active: false, state: "idle", installations: [] },
    });
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => { await Promise.resolve(); });

    const callsBefore = api.get.mock.calls.length;
    await act(async () => {
      setHidden(true);
      setHidden(false);
      await Promise.resolve();
    });

    // No active poll/sync in flight (startConnect was never called) —
    // the backstop must not fire an extra request.
    expect(api.get.mock.calls.length).toBe(callsBefore);
    expect(result.current.connecting).toBe(false);
  });
});
