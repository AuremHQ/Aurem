/**
 * src/__tests__/AdminAuditPage.test.jsx — Part XV: audit logs live updates.
 *
 * Proves AdminAuditPage polls both tabs every 30 s (AUDIT_POLL_MS,
 * same interval as AdminCockpit's POLL_MS and the Admin.jsx
 * supervised-tasks tile), so a new row appears without clicking
 * Refresh — and that the manual Refresh buttons still work, the
 * turns poll pauses while the actions sub-tab is visible, polls are
 * quiet (no loading flash), and the interval is cleaned up on unmount.
 *
 * The API is mocked at ../lib/api (the same module the page imports);
 * no network, no backend. Run with the repo suite (`npm test`) or in
 * isolation: `npx vitest run src/__tests__/AdminAuditPage.test.jsx`.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import AdminAuditPage from "../pages/AdminAuditPage";

vi.mock("../lib/api", () => ({ api: { get: vi.fn() } }));
import { api } from "../lib/api";

const AUDIT_URL = "/admin/audit?limit=100&signals_only=false";
const ACTIONS_URL = "/admin/audit/actions?limit=100";

function turnRow(id) {
  return {
    turn_id: id,
    timestamp: "2026-11-25T10:00:00",
    user_id: "user-abcdef",
    project_id: "proj-1",
    tools_called: ["search"],
    citation_guard_triggered: false,
    system_signals_emitted: [],
    llm_model: "qwen",
    was_retry: false,
  };
}

function actionRow(action = "login") {
  return {
    action,
    ts: "2026-11-25T10:00:00",
    actor: "admin@x",
    target: "u1",
    result: "success",
  };
}

// Mutable holders so a test can "insert a row" between polls.
let turns;
let actions;

function callsFor(url) {
  return api.get.mock.calls.filter(([u]) => u === url).length;
}

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  turns = [turnRow("t1")];
  actions = [actionRow("login")];
  // Return a COPY per call (like a real API response): if the same
  // array reference were returned twice, React would bail out of the
  // re-render (Object.is on state) and the new row would never show.
  api.get.mockImplementation((url) =>
    url.startsWith("/admin/audit/actions")
      ? Promise.resolve({ data: { rows: [...actions] } })
      : Promise.resolve({ data: { rows: [...turns] } }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("AdminAuditPage live polling (Part XV)", () => {
  it("loads turns on mount and re-fetches every 30 s without Refresh", async () => {
    render(<AdminAuditPage />);
    await screen.findByTestId("admin-audit-row-t1");
    expect(callsFor(AUDIT_URL)).toBe(1);

    await advance(30000);
    expect(callsFor(AUDIT_URL)).toBe(2);
    await advance(30000);
    expect(callsFor(AUDIT_URL)).toBe(3);
  });

  it("a new turn appears on poll without clicking Refresh", async () => {
    render(<AdminAuditPage />);
    await screen.findByTestId("admin-audit-row-t1");
    expect(screen.queryByTestId("admin-audit-row-t2")).toBeNull();

    turns.push(turnRow("t2")); // row "inserted" server-side
    await advance(30000);
    await screen.findByTestId("admin-audit-row-t2");
    expect(callsFor(AUDIT_URL)).toBe(2);
  });

  it("quiet polls do not flash the loading state", async () => {
    render(<AdminAuditPage />);
    await screen.findByTestId("admin-audit-row-t1");
    expect(screen.queryByText("Loading…")).toBeNull();
    await advance(30000);
    expect(screen.queryByText("Loading…")).toBeNull();
    expect(callsFor(AUDIT_URL)).toBe(2);
  });

  it("manual Refresh still triggers an immediate refetch", async () => {
    render(<AdminAuditPage />);
    await screen.findByTestId("admin-audit-row-t1");
    expect(callsFor(AUDIT_URL)).toBe(1);
    fireEvent.click(screen.getByTestId("admin-audit-refresh"));
    await act(async () => {});
    expect(callsFor(AUDIT_URL)).toBe(2);
  });

  it("actions sub-tab polls its own endpoint and pauses the turns poll", async () => {
    render(<AdminAuditPage />);
    await screen.findByTestId("admin-audit-row-t1");
    expect(callsFor(AUDIT_URL)).toBe(1);

    fireEvent.click(screen.getByTestId("admin-audit-subtab-actions"));
    await screen.findByTestId("admin-audit-actions-row-0");
    expect(callsFor(ACTIONS_URL)).toBe(1);

    await advance(30000);
    expect(callsFor(ACTIONS_URL)).toBe(2);
    expect(callsFor(AUDIT_URL)).toBe(1); // turns poll paused, not polling blind
    await advance(30000);
    expect(callsFor(ACTIONS_URL)).toBe(3);
    expect(callsFor(AUDIT_URL)).toBe(1);

    // Switching back reloads turns immediately and resumes its poll.
    fireEvent.click(screen.getByTestId("admin-audit-subtab-turns"));
    await screen.findByTestId("admin-audit-row-t1");
    expect(callsFor(AUDIT_URL)).toBe(2);
    await advance(30000);
    expect(callsFor(AUDIT_URL)).toBe(3);
  });

  it("a new admin action appears on poll without clicking Refresh", async () => {
    render(<AdminAuditPage />);
    fireEvent.click(screen.getByTestId("admin-audit-subtab-actions"));
    await screen.findByTestId("admin-audit-actions-row-0");
    expect(screen.queryByTestId("admin-audit-actions-row-1")).toBeNull();

    actions.push(actionRow("bulk-revoke"));
    await advance(30000);
    await screen.findByTestId("admin-audit-actions-row-1");
  });

  it("polling stops on unmount (no interval leak)", async () => {
    const { unmount } = render(<AdminAuditPage />);
    await screen.findByTestId("admin-audit-row-t1");
    expect(callsFor(AUDIT_URL)).toBe(1);
    unmount();
    await advance(90000);
    expect(callsFor(AUDIT_URL)).toBe(1);
  });
});
