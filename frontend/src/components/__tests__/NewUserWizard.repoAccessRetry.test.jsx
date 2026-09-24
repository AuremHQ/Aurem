/**
 * NewUserWizard.repoAccessRetry.test.jsx — 2026-09-12, widened 2026-09-13
 *
 * THIRD confirmed real-user report of the same pattern first flagged
 * as "Rebecca's case": app_installed + app_install_granted succeed,
 * but the gate looked broken/unrecognized right after, and the user
 * skipped. This time reproduced live (screenshot_tool, synthetic-but-
 * realistic fixture) and traced to a DIFFERENT root cause than the
 * 2026-09-11 appInstalls/asGate fix (which is correct and unaffected —
 * see NewUserWizard.connectRefinement.test.jsx, still 3/3 passing):
 *
 * The one-click auto-submit (freshRepos.length===1) fires the INSTANT
 * the grant completes, with zero delay. GitHub's own permission-grant
 * propagation can lag a few seconds behind `installation_active`
 * becoming true, even though the repo already appears in the
 * installation's repo LIST. The backend's real verify call 404s as
 * `installation_no_repo_access` during that window — and because this
 * fires automatically with no user action yet, the very first thing a
 * user sees right after successfully granting access is a scary
 * "doesn't have access, grant it on GitHub" error, which reads as
 * "still broken" and drives the Skip click.
 *
 * 2026-09-13 — 4TH confirmed real user (canndalf@gmail.com, Meta Ads
 * signup): production activity log showed `app_install_granted` fired
 * exactly 3 times before `repo_connect_gate_skipped` — i.e. all 3 of
 * the original retries ran out and STILL hit the transient error,
 * confirming the 9s total budget (3 x 3s) is measurably too short
 * against the same GitHub-side lag this codebase already documents
 * elsewhere for the closely related repo-LIST sync check
 * (`useGitHubConnectStatus.js`'s `BACKGROUND_SYNC_MAX_MS = 2 * 60_000`
 * — that file separately documents an observed worst case of "well
 * over an hour" for the repo-list itself, which this budget does NOT
 * cover; no foreground retry loop reasonably should).
 * Widened to a 120s TIME budget (not a fixed attempt count) — matching
 * the exact ceiling this codebase already trusts for the closely
 * related check, rather than a new invented number — 4s apart, so it
 * keeps quietly retrying via the same friendly "Almost there..."
 * message, never the scary error, until real time has actually run
 * out. This closes the gap between the old 9s and the 120s this
 * codebase already has evidence for; it is NOT proven sufficient for
 * every possible case, only measurably better-grounded than before.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  getToken: () => "fake-token",
  API_BASE: "https://api.test",
}));
vi.mock("../../lib/githubFunnel", () => ({
  trackFunnel: vi.fn(),
  withFunnelParams: (url) => url,
  getFunnelSessionId: () => "sess-1",
}));
vi.mock("../TabBar", () => ({ setActiveProjectId: vi.fn() }));

const mockUseGitHubConnectStatus = vi.fn();
vi.mock("../../hooks/useGitHubConnectStatus", () => ({
  default: () => mockUseGitHubConnectStatus(),
}));

import NewUserWizard from "../NewUserWizard.jsx";
import { api } from "../../lib/api";

const HOOK_STATUS = {
  installation_active: true,
  installations: [{
    installation_id: 42,
    github_login: "repro-org",
    repositories: [{ full_name: "repro-org/demo-repo", default_branch: "main" }],
  }],
  connected_repo: "repro-org/demo-repo",
  repos: [{ full_name: "repro-org/demo-repo", default_branch: "main" }],
  state: "connected",
};

function setupHook() {
  mockUseGitHubConnectStatus.mockReturnValue({
    status: HOOK_STATUS,
    connecting: false,
    timedOut: false,
    denied: false,
    startConnect: vi.fn(),
    retry: vi.fn(),
    refresh: vi.fn().mockResolvedValue(HOOK_STATUS),
    // asGate=true is what this test exercises — mandatory gate path,
    // resuming a FIRST-project user who already has a real, existing
    // installation (not session-fresh) — see the 2026-09-11 fix.
    freshInstallIds: [],
  });
}

const NO_REPO_ACCESS_ERROR = {
  response: { data: { detail: {
    error: "installation_no_repo_access",
    message: "The App installation on @repro-org doesn't have access to repro-org/demo-repo. Grant access on GitHub \u2192 your App \u2192 Configure \u2192 \"Repository access\" (add this repo).",
  } } },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  setupHook();
  api.get.mockResolvedValue({ data: { connected: false } });
  localStorage.clear();
});
afterEach(() => { vi.useRealTimers(); });

async function mountAsGate(onComplete = vi.fn()) {
  render(<NewUserWizard asGate onComplete={onComplete} onSkip={vi.fn()} />);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
  return onComplete;
}

// NOTE: this test harness (asGate + the mount-time auto-submit effect)
// reliably fires `submitRepo` twice right on mount, independent of
// this fix — reproduced even with a plain one-time success mock.
// Confirmed a pre-existing RTL/jsdom effect-timing artifact, NOT a
// real double-submit in the actual running app (verified live via
// screenshot_tool against the real backend: exactly one request per
// attempt). Tests below use blanket (not "-Once") mocks and avoid
// asserting exact call counts so they verify real behavior without
// being brittle to that unrelated harness quirk.
describe("2026-09-12/13 — transient installation_no_repo_access auto-retry", () => {
  it("t_shows_friendly_retry_message_not_scary_error_on_first_failure", async () => {
    api.post.mockRejectedValue(NO_REPO_ACCESS_ERROR);
    await mountAsGate();

    // First failure(s) must show the friendly retry indicator, NEVER
    // the raw red error — this is the exact user-facing symptom fixed.
    await waitFor(() => expect(screen.getByTestId("wizard-repo-access-retrying")).toBeTruthy());
    expect(screen.queryByTestId("wizard-error")).toBeNull();
  });

  it("t_succeeds_on_retry_without_ever_showing_the_error", async () => {
    api.post.mockRejectedValue(NO_REPO_ACCESS_ERROR);
    const onComplete = await mountAsGate();

    await waitFor(() => expect(screen.getByTestId("wizard-repo-access-retrying")).toBeTruthy());
    expect(screen.queryByTestId("wizard-error")).toBeNull();

    // Now let a subsequent attempt succeed, then advance past the
    // retry delay (4s) so it actually fires.
    api.post.mockResolvedValue({ data: { project_id: "px-retry-ok" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
    await waitFor(() => expect(screen.getByTestId("wizard-success-beat")).toBeTruthy());
    expect(screen.queryByTestId("wizard-error")).toBeNull();

    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(onComplete).toHaveBeenCalled();
  });

  it("t_keeps_retrying_quietly_well_past_the_OLD_9s_budget_never_showing_the_error", async () => {
    // THE 4TH-USER REGRESSION TEST: canndalf@gmail.com's real timeline
    // ran out of retries at ~9s (the old budget). Prove that same
    // elapsed time now still shows the calm message, not the error.
    api.post.mockRejectedValue(NO_REPO_ACCESS_ERROR);
    await mountAsGate();
    await waitFor(() => expect(screen.getByTestId("wizard-repo-access-retrying")).toBeTruthy());

    // Advance to ~40s — well past the old 9s budget, well within the
    // new 120s one.
    for (let i = 0; i < 10; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
    }
    expect(screen.getByTestId("wizard-repo-access-retrying")).toBeTruthy();
    expect(screen.queryByTestId("wizard-error")).toBeNull();
  });

  it("t_gives_up_after_the_120s_time_budget_and_shows_the_real_error", async () => {
    api.post.mockRejectedValue(NO_REPO_ACCESS_ERROR);
    await mountAsGate();

    await waitFor(() => expect(screen.getByTestId("wizard-repo-access-retrying")).toBeTruthy());

    // 120s budget, 4s apart — advance well past all of it (~30 ticks).
    for (let i = 0; i < 34; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
    }

    await waitFor(() => expect(screen.getByTestId("wizard-error")).toBeTruthy());
    expect(screen.getByTestId("wizard-error").textContent).toMatch(/doesn.t have access/i);
    expect(screen.queryByTestId("wizard-repo-access-retrying")).toBeNull();
    // No infinite silent retry loop — a bounded number of attempts,
    // not one per render forever. ~120s/4s + mount attempts + harness
    // double-fire headroom.
    expect(api.post.mock.calls.length).toBeLessThanOrEqual(45);
  });

  it("t_unrelated_errors_never_trigger_a_retry", async () => {
    api.post.mockRejectedValue({
      response: { data: { detail: {
        error: "github_probe_failed",
        message: "GitHub is unreachable right now.",
      } } },
    });
    await mountAsGate();

    await waitFor(() => expect(screen.getByTestId("wizard-error")).toBeTruthy());
    expect(screen.queryByTestId("wizard-repo-access-retrying")).toBeNull();

    // A different, non-propagation-lag error must never schedule a
    // retry — call count should not grow after advancing time.
    const countAfterMount = api.post.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.post.mock.calls.length).toBe(countAfterMount);
  });
});
