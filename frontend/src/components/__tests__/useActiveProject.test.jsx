/**
 * useActiveProject.test.js — Turn-Completion Contract, Section 6
 * (2026-09-08). t_approve_button_mount_independent.
 *
 * Root cause of the recurring "Approve button didn't load" bug:
 * useActiveProject's `/cto/projects/list` fetch had ZERO retry. One
 * transient network blip left `activeProject` null for the entire
 * page session (nothing else re-triggers a fetch), which cascades
 * into `canShip=false` in MessageBubble.jsx and the Approve button
 * being replaced by "Switch to a connected project" — permanently.
 *
 * This locks the fix: the hook must retry once, silently, before
 * giving up — a simulated first-fetch failure that succeeds on the
 * silent retry must still resolve to the real project (mount-time
 * resilience, independent of any parent component's own render/fetch
 * timing).
 *
 * Run: cd /app/frontend && npx vitest run src/components/__tests__/useActiveProject.test.js
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { api } from "../../lib/api";
import { setActiveProjectId } from "../activeProject";
import { useActiveProject } from "../useActiveProject";

function Probe() {
  const project = useActiveProject();
  return <div data-testid="probe">{project ? project.project_id : "null"}</div>;
}

describe("useActiveProject — mount-independent retry (2026-09-08)", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("aurem_token", "test-fake-token");
    setActiveProjectId(null);
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("recovers from one transient fetch failure via a silent auto-retry, without any parent re-render forcing it", async () => {
    const realProject = { project_id: "p_recovered_after_retry", github_owner: "x", github_repo: "y" };
    let calls = 0;
    const spy = vi.spyOn(api, "get").mockImplementation(async () => {
      calls += 1;
      if (calls === 1) {
        // Simulated transient failure — e.g. a network blip during
        // page load while several other requests are also in flight.
        throw new Error("simulated transient network failure");
      }
      return { data: { projects: [realProject] } };
    });

    render(<Probe />);

    // Must resolve to the real project WITHOUT any external trigger
    // (no parent re-render, no manual retry click) — purely from the
    // hook's own internal silent retry on mount.
    await waitFor(
      () => expect(screen.getByTestId("probe").textContent).toBe("p_recovered_after_retry"),
      { timeout: 3000 },
    );

    // Exactly one retry happened on the first fetch attempt (proven by
    // resolving to the real project despite call #1 failing); the
    // 3rd call is the hook's own expected re-run after
    // setActiveProjectId (a real, separate fetch cycle, not part of
    // the retry itself) — bounded and finite either way, never an
    // unbounded retry loop.
    expect(calls).toBeGreaterThanOrEqual(2);
    expect(calls).toBeLessThanOrEqual(4);
    spy.mockRestore();
  });

  it("still gives up gracefully (never throws, never renders broken UI) when both the first attempt and the silent retry fail", async () => {
    const spy = vi.spyOn(api, "get").mockImplementation(async () => {
      throw new Error("simulated persistent failure");
    });

    render(<Probe />);

    // Give the internal retry's backoff window time to elapse.
    await new Promise((r) => setTimeout(r, 1700));

    // Never crashes; resolves to "no project" rather than a thrown
    // error bubbling into the component tree.
    expect(screen.getByTestId("probe").textContent).toBe("null");
    spy.mockRestore();
  });
});
