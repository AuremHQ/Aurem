/**
 * LiveTaskPopup.part13_sse.test.jsx — Part XIII · 4(a).
 *
 * The popup now subscribes to the task SSE stream
 * (GET /cto/tasks/{task_id}/stream) instead of polling every 2s,
 * with the 2s poll kept only as a fallback when the stream can't
 * open or drops. Also locks in:
 *   • onDone fires on "done" OR "completed" (was "done" only).
 *   • onDone fires after a taskId change without a key remount
 *     (onDoneFiredRef resets per task).
 *   • Retry button on failed tasks → POST /tasks/{id}/retry,
 *     handing the new task_id to onRetry.
 *   • Rollback / Approve / Ship are NOT in this popup (no loop_id
 *     on CTO tasks; Approve/Ship belong to Loop sessions).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { api } from "../../lib/api";
import LiveTaskPopup from "../LiveTaskPopup.jsx";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  API_BASE: "/api/aurem-dev",
}));

const RUNNING_TASK = {
  task_id: "t1",
  status: "running",
  steps: [{ kind: "phase_read", step: "Reading repo", ts: 1 }],
  files_changed: [], vanguard_findings: [], files_read: [],
};
const DONE_TASK = {
  ...RUNNING_TASK,
  status: "done",
  commit_sha: "abc1234",
  edited_files: { files: [{ path: "a.py" }] },
};
const COMPLETED_TASK = { ...RUNNING_TASK, status: "completed" };
const FAILED_TASK = {
  ...RUNNING_TASK,
  status: "failed",
  error: "worker crashed",
};

// ── Controllable SSE stub ──────────────────────────────────────────
// fetch resolves to a stream whose reader.read() only resolves when
// the test pushes a frame (or finishes). Lets the test prove the
// popup stays on the stream (no polls) and reacts to frames.
function createSseHarness() {
  const enc = new TextEncoder();
  const pending = [];
  const reader = {
    read: vi.fn(() => new Promise((resolve) => { pending.push(resolve); })),
    cancel: vi.fn(() => Promise.resolve()),
  };
  const fetchMock = vi.fn(() => Promise.resolve({
    ok: true,
    body: { getReader: () => reader },
  }));
  const pushFrame = async (raw) => {
    await waitFor(() => expect(pending.length).toBeGreaterThan(0));
    pending.shift()({ value: enc.encode(raw), done: false });
    await act(async () => {});
  };
  const finishStream = async () => {
    await waitFor(() => expect(pending.length).toBeGreaterThan(0));
    pending.shift()({ value: undefined, done: true });
    await act(async () => {});
  };
  return { fetchMock, reader, pushFrame, finishStream };
}

let sse;
let realFetch;

beforeEach(() => {
  vi.clearAllMocks();
  realFetch = global.fetch;
  sse = createSseHarness();
  global.fetch = sse.fetchMock;
  localStorage.clear();
});

afterEach(() => {
  global.fetch = realFetch;
  vi.useRealTimers();
  vi.clearAllTimers();
});

const flush = () => act(async () => {});

describe("Part XIII · 4(a) — SSE replaces the 2s poll", () => {
  it("opens the task SSE stream and issues NO further GETs while it is open", async () => {
    vi.useFakeTimers();
    api.get.mockResolvedValue({ data: { task: RUNNING_TASK } });
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await vi.advanceTimersByTimeAsync(0); // flush initial GET + stream open
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(sse.fetchMock).toHaveBeenCalledWith(
      "/api/aurem-dev/cto/tasks/t1/stream",
      expect.objectContaining({ method: "GET" }),
    );
    // 10s pass with the stream open — the old code would have polled 5×.
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.get).toHaveBeenCalledTimes(1);
  });

  it("a terminal done frame triggers one final GET and fires onDone", async () => {
    api.get
      .mockResolvedValueOnce({ data: { task: RUNNING_TASK } })
      .mockResolvedValue({ data: { task: DONE_TASK } });
    const onDone = vi.fn();
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={onDone} />);
    await waitFor(() => expect(sse.fetchMock).toHaveBeenCalled());
    await sse.pushFrame('data: {"type":"done","step":"Done — abc1234"}\n\n');
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ status: "done" }));
    // Exactly 2 GETs: initial load + final refresh after the done frame.
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("a live step frame appends to the tape (no extra GET)", async () => {
    api.get.mockResolvedValue({ data: { task: RUNNING_TASK } });
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await waitFor(() => expect(sse.fetchMock).toHaveBeenCalled());
    await sse.pushFrame('data: {"kind":"phase_think","step":"Thinking","ts":2}\n\n');
    await waitFor(() => expect(
      screen.getByTestId("ltp-livetape").textContent,
    ).toContain("Thinking"));
    expect(api.get).toHaveBeenCalledTimes(1);
  });

  it("falls back to 2s polling when the stream cannot open", async () => {
    vi.useFakeTimers();
    global.fetch = vi.fn(() => Promise.reject(new Error("network down")));
    api.get.mockResolvedValue({ data: { task: RUNNING_TASK } });
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await vi.advanceTimersByTimeAsync(0); // initial GET + failed stream open
    expect(api.get).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2100); // first fallback tick
    expect(api.get).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2000); // second fallback tick
    expect(api.get).toHaveBeenCalledTimes(3);
  });
});

describe("Part XIII · 4(a) — onDone on 'done' OR 'completed'", () => {
  it("fires onDone when the task is already 'completed' on first load", async () => {
    api.get.mockResolvedValue({ data: { task: COMPLETED_TASK } });
    const onDone = vi.fn();
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={onDone} />);
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ status: "completed" }),
    );
    // No stream opened for an already-terminal task.
    expect(sse.fetchMock).not.toHaveBeenCalled();
  });

  it("fires onDone when the task is already 'done' on first load", async () => {
    api.get.mockResolvedValue({ data: { task: DONE_TASK } });
    const onDone = vi.fn();
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={onDone} />);
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(sse.fetchMock).not.toHaveBeenCalled();
  });

  it("fires onDone for the SECOND task after a taskId change without remount", async () => {
    api.get.mockImplementation((url) => {
      if (url.includes("t2")) return Promise.resolve({ data: { task: { ...DONE_TASK, task_id: "t2" } } });
      return Promise.resolve({ data: { task: RUNNING_TASK } });
    });
    const onDone = vi.fn();
    const { rerender } = render(
      <LiveTaskPopup taskId="t1" onClose={() => {}} onDone={onDone} />,
    );
    await waitFor(() => expect(sse.fetchMock).toHaveBeenCalledTimes(1));
    expect(onDone).not.toHaveBeenCalled();
    rerender(<LiveTaskPopup taskId="t2" onClose={() => {}} onDone={onDone} />);
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ task_id: "t2", status: "done" }),
    );
  });
});

describe("Part XIII · 4(a) — Retry control", () => {
  it("shows a Retry button on failed tasks and POSTs /tasks/{id}/retry", async () => {
    api.get.mockResolvedValue({ data: { task: FAILED_TASK } });
    api.post.mockResolvedValue({ data: { ok: true, task_id: "t_new", retry_of: "t1" } });
    const onRetry = vi.fn();
    render(
      <LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} onRetry={onRetry} />,
    );
    const btn = await screen.findByTestId("ltp-retry-btn");
    expect(btn.textContent).toMatch(/retry task/i);
    fireEvent.click(btn);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/cto/tasks/t1/retry",
      {},
    ));
    await waitFor(() => expect(onRetry).toHaveBeenCalledWith("t_new"));
  });

  it("shows a retry error when the retry POST fails", async () => {
    api.get.mockResolvedValue({ data: { task: FAILED_TASK } });
    api.post.mockRejectedValue({ response: { data: { detail: "Only failed tasks can be retried" } } });
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    fireEvent.click(await screen.findByTestId("ltp-retry-btn"));
    await waitFor(() => expect(screen.getByTestId("ltp-retry-error")).toBeInTheDocument());
    expect(screen.getByTestId("ltp-retry-error").textContent)
      .toContain("Only failed tasks can be retried");
  });

  it("shows NO Retry button while the task is running", async () => {
    api.get.mockResolvedValue({ data: { task: RUNNING_TASK } });
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("live-task-popup")).toBeInTheDocument());
    expect(screen.queryByTestId("ltp-retry-btn")).not.toBeInTheDocument();
  });

  it("has no Rollback / Approve / Ship controls (none exist for CTO tasks)", async () => {
    api.get.mockResolvedValue({ data: { task: FAILED_TASK } });
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("live-task-popup")).toBeInTheDocument());
    expect(screen.queryByTestId("ltp-rollback-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ltp-approve-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ltp-ship-btn")).not.toBeInTheDocument();
  });
});
