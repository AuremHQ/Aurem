/**
 * Bug 8 fix (Sep 17 2026 regression spec) — Analytics showed a flat
 * "Failed: 35" with no way to know *why* tasks failed. The backend
 * now tags every failure with a reason code (failure_taxonomy.py)
 * and product-analytics returns tasks.failed_by_reason. This test
 * covers the frontend render of that breakdown.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const get = vi.fn();
vi.mock("../../lib/api", () => ({ api: { get: (...args) => get(...args) } }));

// Analytics wraps everything in Shell/RailShell, which pull in auth,
// token-refresh polling, and health-check calls unrelated to this
// bug. Stub them as pass-throughs so the test is isolated to the
// Analytics page's own render logic.
vi.mock("../../components/Shell", () => ({
  __esModule: true,
  default: ({ children }) => <div>{children}</div>,
  PageHeader: ({ title }) => <h1>{title}</h1>,
  useChatSession: () => ({}),
}));
vi.mock("../../components/nav/RailShell", () => ({
  __esModule: true,
  default: ({ children }) => <div>{children}</div>,
}));

import Analytics from "../Analytics.jsx";

const baseAnalytics = {
  ok: true,
  period_days: 30,
  users: { total: 10, dau: 2, wau: 5, mau: 8, new_this_week: 1, by_tier: {} },
  tasks: {
    total: 40,
    done: 5,
    failed: 5,
    failed_by_reason: { TIMEOUT: 3, HALLUCINATION_GATE: 2 },
    success_rate_pct: 12.5,
    maxx_mode: 0,
    tokens_burned: 0,
  },
  modes: { distribution: [] },
  trend: { dau_14d: [] },
};

beforeEach(() => {
  get.mockReset();
});

describe("Analytics — Failed task reason breakdown (Bug 8 fix)", () => {
  it("renders each failure reason code with its count", async () => {
    get.mockResolvedValue({ data: baseAnalytics });
    render(<Analytics />);
    await waitFor(() => expect(screen.getByTestId("analytics-failed-by-reason")).toBeInTheDocument());
    expect(screen.getByTestId("analytics-failed-reason-TIMEOUT").textContent).toMatch(/3/);
    expect(screen.getByTestId("analytics-failed-reason-HALLUCINATION_GATE").textContent).toMatch(/2/);
  });

  it("does not render the breakdown block when there are no failures", async () => {
    get.mockResolvedValue({
      data: { ...baseAnalytics, tasks: { ...baseAnalytics.tasks, failed: 0, failed_by_reason: {} } },
    });
    render(<Analytics />);
    await waitFor(() => expect(screen.getByTestId("analytics-task-health")).toBeInTheDocument());
    expect(screen.queryByTestId("analytics-failed-by-reason")).toBeNull();
  });
});
