/**
 * FixProgressDrawer.batch_link.test.jsx — FIX_one_branch_for_all_fixes
 *
 * The drawer used to show one "View PR" link per completed fix row
 * (one PR per finding, under the old one-branch-per-fix scheme). Now
 * every fix in a job shares ONE branch + ONE PR, surfaced via a
 * single "batch-info" SSE event. This proves:
 *   - a job that received a "batch-info" event renders exactly ONE
 *     shared-branch banner with the right PR link and branch name;
 *   - a legacy job (no "batch-info" event ever arrived — e.g. an
 *     in-flight job started before this change) shows NO shared
 *     banner and still renders the old per-fix "View PR" link, so
 *     nothing regresses for jobs already streaming.
 */
import React from "react";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, act, screen } from "@testing-library/react";
import { FixJobProvider, useFixJob } from "../FixJobContext.jsx";
import FixProgressDrawer from "../FixProgressDrawer.jsx";

class MockEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    MockEventSource.instances.push(this);
  }
  addEventListener(type, cb) { this.listeners[type] = cb; }
  close() { this.closed = true; }
  emit(type, data) {
    this.listeners[type]?.({ data: JSON.stringify(data) });
  }
}
MockEventSource.instances = [];

function Harness() {
  const { startJob } = useFixJob();
  window.__startJob = startJob;
  return <FixProgressDrawer />;
}

describe("FixProgressDrawer — shared fix-batch link", () => {
  let originalEventSource;

  beforeEach(() => {
    localStorage.clear();
    MockEventSource.instances = [];
    originalEventSource = global.EventSource;
    global.EventSource = MockEventSource;
  });

  afterEach(() => {
    global.EventSource = originalEventSource;
  });

  it("shows ONE shared-branch banner when a batch-info event arrives", () => {
    render(<FixJobProvider><Harness /></FixJobProvider>);
    act(() => { window.__startJob({ job_id: "job-batch-1", total: 2 }); });

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.emit("phase", {
        phase: "batch-info",
        pr_url: "https://github.com/o/r/pull/42",
        branch: "aurem/fixes-deadbeef",
        batch_id: "fb_1", fix_count: 2,
      });
    });

    const link = screen.getByTestId("fix-batch-link");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe("https://github.com/o/r/pull/42");
    expect(link.textContent).toContain("aurem/fixes-deadbeef");
    expect(link.textContent).toContain("1 pull request");
  });

  it("renders no shared-branch banner for a legacy job, but keeps the per-fix link", () => {
    render(<FixJobProvider><Harness /></FixJobProvider>);
    act(() => { window.__startJob({ job_id: "job-legacy-1", total: 1 }); });

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.emit("phase", {
        phase: "fix-done", ok: true, finding_id: "f1",
        commit_sha: "abc1234", html_url: "https://github.com/o/r/commit/abc1234",
        pr_url: "https://github.com/o/r/pull/7",
        file: "a.py", rule_id: "rule_a", fix_index: 1, fix_total: 1,
      });
    });

    expect(screen.queryByTestId("fix-batch-link")).toBeNull();
    // Legacy per-fix commit link still renders (backward compat).
    expect(screen.getByTestId("fix-row-commit-f1")).toBeTruthy();
  });
});
