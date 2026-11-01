/**
 * FixProgressDrawer.undoRevert.test.jsx — Part 2b R6.
 *
 * There were NO revert HTTP routes and no frontend UI to call them.
 * This proves the "Undo this fix" button on a single completed row
 * and the "Undo all" button on a terminal batch both call the real
 * revert endpoints with a confirm step, and reflect success/failure
 * back into the UI.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act, screen, fireEvent, waitFor } from "@testing-library/react";
import { FixJobProvider, useFixJob } from "../FixJobContext.jsx";

const apiPostMock = vi.fn();
vi.mock("../../lib/api", () => ({
  api: { post: (...args) => apiPostMock(...args) },
}));
const toastMock = vi.fn();
vi.mock("../Toast", () => ({ toast: (...args) => toastMock(...args) }));

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

describe("FixProgressDrawer — Part 2b R6 undo/revert", () => {
  let originalEventSource;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    MockEventSource.instances = [];
    originalEventSource = global.EventSource;
    global.EventSource = MockEventSource;
  });

  afterEach(() => {
    global.EventSource = originalEventSource;
  });

  it("undoes ONE fix via confirm -> api.post -> reverted state", async () => {
    render(<FixJobProvider><Harness /></FixJobProvider>);
    act(() => { window.__startJob({ job_id: "job-undo-1", total: 1 }); });
    const es = MockEventSource.instances[MockEventSource.instances.length - 1];

    act(() => {
      es.emit("phase", {
        phase: "fix-done", ok: true, finding_id: "f1",
        commit_sha: "abc1234", html_url: "https://github.com/o/r/commit/abc1234",
        file: "a.py", rule_id: "rule_a", fix_id: "fx_1",
        fix_index: 1, fix_total: 1,
      });
    });

    const undoBtn = screen.getByTestId("fix-row-undo-f1");
    fireEvent.click(undoBtn);
    expect(screen.getByTestId("fix-row-undo-confirm-f1")).toBeTruthy();

    apiPostMock.mockResolvedValue({ data: { ok: true, reverted: true } });
    fireEvent.click(screen.getByTestId("fix-row-undo-confirm-f1"));

    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/fix-pipeline/fixes/fx_1/revert"));
    await waitFor(() => expect(screen.getByTestId("fix-row-undone-f1")).toBeTruthy());
  });

  it("undoes the WHOLE batch via confirm -> api.post with confirm:true -> reverted banner", async () => {
    render(<FixJobProvider><Harness /></FixJobProvider>);
    act(() => { window.__startJob({ job_id: "job-undo-2", total: 1 }); });
    const es = MockEventSource.instances[MockEventSource.instances.length - 1];

    act(() => {
      es.emit("phase", {
        phase: "batch-info", pr_url: "https://github.com/o/r/pull/9",
        branch: "aurem/fixes-deadbeef", batch_id: "fb_1", fix_count: 1,
      });
    });
    act(() => {
      es.emit("phase", {
        phase: "fix-done", ok: true, finding_id: "f1",
        commit_sha: "abc1234", html_url: "https://x",
        file: "a.py", rule_id: "rule_a", fix_id: "fx_1",
        fix_index: 1, fix_total: 1,
      });
    });
    act(() => {
      es.emit("phase", {
        phase: "done", completed: 1, failed: 0, total: 1,
        message: "done", can_restart: false,
      });
    });

    const undoAllBtn = screen.getByTestId("fix-batch-undo-all");
    fireEvent.click(undoAllBtn);
    expect(screen.getByTestId("fix-batch-undo-all-confirm")).toBeTruthy();

    apiPostMock.mockResolvedValue({ data: { ok: true, pr_closed: true } });
    fireEvent.click(screen.getByTestId("fix-batch-undo-all-confirm"));

    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith(
      "/fix-pipeline/batches/fb_1/revert", { confirm: true },
    ));
    await waitFor(() => expect(screen.getByTestId("fix-batch-undone")).toBeTruthy());
  });

  it("shows a toast and a retry affordance when the revert call fails", async () => {
    render(<FixJobProvider><Harness /></FixJobProvider>);
    act(() => { window.__startJob({ job_id: "job-undo-3", total: 1 }); });
    const es = MockEventSource.instances[MockEventSource.instances.length - 1];

    act(() => {
      es.emit("phase", {
        phase: "fix-done", ok: true, finding_id: "f1",
        commit_sha: "abc1234", html_url: "https://x",
        file: "a.py", rule_id: "rule_a", fix_id: "fx_1",
        fix_index: 1, fix_total: 1,
      });
    });

    apiPostMock.mockRejectedValue({ message: "revert_failed" });
    fireEvent.click(screen.getByTestId("fix-row-undo-f1"));
    fireEvent.click(screen.getByTestId("fix-row-undo-confirm-f1"));

    await waitFor(() => expect(toastMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Undo failed — retry")).toBeTruthy());
    expect(screen.queryByTestId("fix-row-undone-f1")).toBeNull();
  });
});
