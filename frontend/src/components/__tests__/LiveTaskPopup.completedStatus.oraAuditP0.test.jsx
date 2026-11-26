/**
 * LiveTaskPopup.completedStatus.oraAuditP0.test.jsx — ORA audit P0
 * (claim #7, LiveTaskPopup half).
 *
 * The popup's TERMINAL set already included "completed", but the
 * done-path fired only on status === "done". A task that finished
 * with status "completed" therefore stopped polling silently: no
 * onDone (no edited_files attached in ChatPanel), no 5s dismiss —
 * the popup sat stuck on screen. The fix treats "completed" exactly
 * like "done".
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { api } from "../../lib/api";
import LiveTaskPopup from "../LiveTaskPopup.jsx";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn() },
}));

const BASE_TASK = {
  steps: [],
  files_changed: [],
  vanguard_findings: [],
  files_read: [],
  edited_files: [{ path: "src/a.py", diff: "+x" }],
};

describe("P0 claim #7 · LiveTaskPopup completes on status 'completed'", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  async function flushMicrotasks() {
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  }

  it("status 'completed' fires onDone once and dismisses after 5s, like 'done'", async () => {
    api.get.mockResolvedValue({ data: { task: { ...BASE_TASK, status: "completed" } } });
    const onDone = vi.fn();
    const onClose = vi.fn();
    render(<LiveTaskPopup taskId="t-completed" onClose={onClose} onDone={onDone} />);
    await flushMicrotasks();

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ status: "completed", edited_files: BASE_TASK.edited_files }),
    );
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("status 'done' still fires onDone once and dismisses (no regression)", async () => {
    api.get.mockResolvedValue({ data: { task: { ...BASE_TASK, status: "done" } } });
    const onDone = vi.fn();
    const onClose = vi.fn();
    render(<LiveTaskPopup taskId="t-done" onClose={onClose} onDone={onDone} />);
    await flushMicrotasks();

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ status: "done" }));
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("a non-terminal status fires neither onDone nor onClose", async () => {
    api.get.mockResolvedValue({ data: { task: { ...BASE_TASK, status: "running" } } });
    const onDone = vi.fn();
    const onClose = vi.fn();
    render(<LiveTaskPopup taskId="t-running" onClose={onClose} onDone={onDone} />);
    await flushMicrotasks();
    await act(async () => { vi.advanceTimersByTime(5000); });

    expect(onDone).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("a completed task still renders (popup is visible until dismiss)", async () => {
    api.get.mockResolvedValue({ data: { task: { ...BASE_TASK, status: "completed" } } });
    render(<LiveTaskPopup taskId="t-visible" onClose={() => {}} onDone={() => {}} />);
    await flushMicrotasks();
    // The popup renders the task once the poll resolves; exact copy
    // is not the point — it must not render an error/blank state.
    expect(document.body.textContent.length).toBeGreaterThan(0);
    expect(screen.queryByText(/failed|error/i)).not.toBeInTheDocument();
  });
});
