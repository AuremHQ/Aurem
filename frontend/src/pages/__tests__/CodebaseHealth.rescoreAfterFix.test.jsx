/**
 * CodebaseHealth.rescoreAfterFix.test.jsx — UI-fix C5 (2026-10)
 *
 * Bug fix: the `aurem:finding-fixed` listener used to bump the score
 * by a made-up "+2" with no basis in the repo's real state. It now
 * does an optimistic local removal only, then verifies the real score
 * via a free re-scan (scanning never charges — only Fix does) and
 * shows "Score verified: before → after".
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../../components/BulkFixConfirmModal", () => ({ default: () => null }));
vi.mock("../../components/NewUserWizard", () => ({ default: () => null }));

import CodebaseHealth from "../CodebaseHealth.jsx";
import { api } from "../../lib/api";

const FIRST_SCAN = {
  score: 60, label: "NEEDS ATTENTION", tone: "amber", total: 1,
  breakdown: {
    security: {
      score: 60, total: 1, counts: { critical: 1, high: 0, medium: 0, low: 0 },
      findings: [{ id: "f1", severity: "critical", file: "a.py", line: 1, title: "x", description: "x" }],
    },
  },
};
const VERIFIED_SCAN = {
  score: 92, label: "HEALTHY", tone: "green", total: 0,
  breakdown: { security: { score: 92, total: 0, counts: { critical: 0, high: 0, medium: 0, low: 0 }, findings: [] } },
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/codebase-health?project_id=p1");
  api.get.mockResolvedValue({ data: {} });
});

function renderScanned() {
  render(<MemoryRouter><CodebaseHealth /></MemoryRouter>);
}

describe("UI-fix C5 · real re-check after a fix, no more fake +2", () => {
  it("drops the fixed finding immediately (no +2 guess), then shows the real verified score once the re-check resolves", async () => {
    let resolveVerify;
    api.post.mockResolvedValueOnce({ data: FIRST_SCAN });
    api.post.mockReturnValueOnce(new Promise((res) => { resolveVerify = res; }));
    renderScanned();
    const { fireEvent } = await import("@testing-library/react");
    await screen.findByTestId("scan-security"); // empty state first
    fireEvent.click(screen.getByTestId("scan-security"));
    await waitFor(() => expect(screen.getByTestId("health-score-number")).toHaveTextContent("60"));

    act(() => {
      window.dispatchEvent(new CustomEvent("aurem:finding-fixed", { detail: { finding_id: "f1" } }));
    });

    // Finding gone from the list right away, but the score holds at the
    // real last-known number (60) — never a made-up +2 (62) — while the
    // real verification re-scan is still in flight.
    await waitFor(() => expect(screen.queryByText("x")).not.toBeInTheDocument());
    expect(screen.getByTestId("health-score-number")).toHaveTextContent("60");
    expect(api.post).toHaveBeenLastCalledWith("/codebase-health/scan", {
      project_id: "p1", categories: ["security"],
    });

    // The verification re-scan resolves with the repo's real new score.
    act(() => { resolveVerify({ data: VERIFIED_SCAN }); });
    await waitFor(() => expect(screen.getByTestId("health-rescore-banner")).toHaveTextContent("Score verified: 60 → 92"));
    expect(screen.getByTestId("health-score-number")).toHaveTextContent("92");
  });
});
