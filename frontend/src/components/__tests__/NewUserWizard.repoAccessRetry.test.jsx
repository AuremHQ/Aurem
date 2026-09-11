/**
 * NewUserWizard.repoAccessRetry.test.jsx — 2026-09-12
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
 * Fix: auto-retry up to 3 times (3s apart) for this ONE specific error
 * code before ever surfacing it as a real failure, showing a friendly
 * "Almost there..." message in the interim instead of the red error.
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
describe("2026-09-12 — transient installation_no_repo_access auto-retry", () => {
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
    // retry delay so it actually fires.
    api.post.mockResolvedValue({ data: { project_id: "px-retry-ok" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await waitFor(() => expect(screen.getByTestId("wizard-success-beat")).toBeTruthy());
    expect(screen.queryByTestId("wizard-error")).toBeNull();

    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(onComplete).toHaveBeenCalled();
  });

  it("t_gives_up_after_max_retries_and_shows_the_real_error", async () => {
    api.post.mockRejectedValue(NO_REPO_ACCESS_ERROR);
    await mountAsGate();

    await waitFor(() => expect(screen.getByTestId("wizard-repo-access-retrying")).toBeTruthy());

    // 3 retries max, 3s apart — advance well past all of them.
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });

    await waitFor(() => expect(screen.getByTestId("wizard-error")).toBeTruthy());
    expect(screen.getByTestId("wizard-error").textContent).toMatch(/doesn.t have access/i);
    expect(screen.queryByTestId("wizard-repo-access-retrying")).toBeNull();
    // No infinite silent retry loop — a bounded number of attempts,
    // not one per render forever.
    expect(api.post.mock.calls.length).toBeLessThanOrEqual(8);
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
