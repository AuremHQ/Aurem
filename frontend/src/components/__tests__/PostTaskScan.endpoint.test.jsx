/**
 * PostTaskScan — polls the backend for the post-task scan.
 *
 * Bug (2026-09-29 admin audit): the component called
 * /cto/projects/{pid}/tasks/{tid}/scan, a route that does not exist
 * (the backend serves /cto/tasks/{task_id}/scan). Every poll 404'd, the
 * error was swallowed, and post-task security warnings never appeared.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

const get = vi.fn();
vi.mock("../../lib/api", () => ({ api: { get: (...a) => get(...a) } }));

import PostTaskScan from "../PostTaskScan";

beforeEach(() => {
  vi.useFakeTimers();
  get.mockReset();
});
afterEach(() => vi.useRealTimers());

const poll = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2100);
  });
};

describe("<PostTaskScan>", () => {
  it("polls the real backend route /cto/tasks/{id}/scan", async () => {
    get.mockResolvedValue({ data: { ok: true, status: "done", scan: null } });
    render(<PostTaskScan taskId="task-42" onFixRequest={() => {}} />);
    await poll();
    expect(get).toHaveBeenCalledWith("/cto/tasks/task-42/scan");
    expect(get.mock.calls.every(([p]) => !p.includes("/projects/"))).toBe(true);
  });

  it("shows the warning card when the scan found issues", async () => {
    get.mockResolvedValue({
      data: {
        ok: true,
        status: "done",
        scan: { issues: [{ severity: "HIGH", message: "eval() on user input", file: "app.py", line: 12, snippet: "eval(x)" }] },
      },
    });
    render(<PostTaskScan taskId="task-42" onFixRequest={() => {}} />);
    await poll();
    expect(screen.getByTestId("post-task-scan")).toBeTruthy();
    expect(screen.getByText(/eval\(\) on user input/)).toBeTruthy();
  });

  it("renders nothing while the scan has no issues", async () => {
    get.mockResolvedValue({ data: { ok: true, status: "done", scan: null } });
    render(<PostTaskScan taskId="task-42" onFixRequest={() => {}} />);
    await poll();
    expect(screen.queryByTestId("post-task-scan")).toBeNull();
  });

  it("does not poll without a task id", async () => {
    render(<PostTaskScan taskId={null} onFixRequest={() => {}} />);
    await poll();
    expect(get).not.toHaveBeenCalled();
  });
});
