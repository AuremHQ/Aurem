/**
 * ProjectChanges.test.jsx — Part 2c B4/B5.
 * Empty state, rendered rows, free-tier locked-count line, hostile
 * summary rendered as plain text (not HTML), and a non-github.com PR
 * URL never rendered as a clickable link.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import ProjectChanges from "../ProjectChanges.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPage(projectId = "proj1") {
  return render(
    <MemoryRouter initialEntries={[`/projects/${projectId}/changes`]}>
      <Routes>
        <Route path="/projects/:projectId/changes" element={<ProjectChanges />} />
      </Routes>
    </MemoryRouter>,
  );
}

function versionRow(overrides = {}) {
  return {
    version_id: "pv_1", n: 3, merged_sha: "abc1234def5678900000000000000000000000",
    pr_url: "https://github.com/o/r/pull/9", branch: "b",
    source: "ship_pr", summary: "Added dark mode", merged_at: 1700000000,
    status: "merged",
    ...overrides,
  };
}

describe("ProjectChanges page", () => {
  it("shows 'No changes landed yet.' when the list is empty", async () => {
    api.get.mockResolvedValue({ data: { ok: true, versions: [], total: 0, locked_count: 0 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("changes-empty-state")).toBeInTheDocument());
    expect(screen.getByTestId("changes-empty-state").textContent).toBe("No changes landed yet.");
  });

  it("renders 3 rows with v-badge, date, summary, and short sha", async () => {
    const versions = [
      versionRow({ version_id: "pv_1", n: 3 }),
      versionRow({ version_id: "pv_2", n: 2 }),
      versionRow({ version_id: "pv_3", n: 1 }),
    ];
    api.get.mockResolvedValue({ data: { ok: true, versions, total: 3, locked_count: 0 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("changes-list")).toBeInTheDocument());

    expect(screen.getByTestId("changes-badge-pv_1").textContent).toBe("v3");
    expect(screen.getByTestId("changes-summary-pv_1").textContent).toBe("Added dark mode");
    expect(screen.getByTestId("changes-sha-pv_1").textContent).toBe("abc1234");
    expect(screen.queryAllByTestId(/^changes-row-/)).toHaveLength(3);
  });

  it("shows the free-tier locked-count line verbatim", async () => {
    const versions = Array.from({ length: 10 }, (_, i) =>
      versionRow({ version_id: `pv_${i}`, n: 10 - i }));
    api.get.mockResolvedValue({ data: { ok: true, versions, total: 15, locked_count: 5 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("changes-locked-line")).toBeInTheDocument());
    expect(screen.getByTestId("changes-locked-line").textContent).toBe(
      "Showing your latest 10 of 15.",
    );
    // No invented claims anywhere on the page.
    expect(screen.queryByText(/unlimited/i)).toBeNull();
    expect(screen.queryByText(/uptime/i)).toBeNull();
    expect(screen.queryByText(/refund/i)).toBeNull();
  });

  it("renders a hostile summary as plain text, never as HTML", async () => {
    const hostile = "<img src=x onerror=alert(1)>Pwned";
    const versions = [versionRow({ version_id: "pv_1", summary: hostile })];
    api.get.mockResolvedValue({ data: { ok: true, versions, total: 1, locked_count: 0 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("changes-summary-pv_1")).toBeInTheDocument());

    const el = screen.getByTestId("changes-summary-pv_1");
    expect(el.textContent).toBe(hostile);
    // The literal tag must never be parsed into a real <img> element —
    // proof the text was NOT injected via dangerouslySetInnerHTML.
    expect(el.querySelector("img")).toBeNull();
  });

  it("renders a github.com https PR link as clickable", async () => {
    const versions = [versionRow({ version_id: "pv_1", pr_url: "https://github.com/o/r/pull/42" })];
    api.get.mockResolvedValue({ data: { ok: true, versions, total: 1, locked_count: 0 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("changes-pr-link-pv_1")).toBeInTheDocument());
    expect(screen.getByTestId("changes-pr-link-pv_1").getAttribute("href")).toBe(
      "https://github.com/o/r/pull/42",
    );
  });

  it("never renders a non-github.com (or non-https) URL as a link", async () => {
    const versions = [
      versionRow({ version_id: "pv_evil1", pr_url: "https://evil.com/github.com/pull/1" }),
      versionRow({ version_id: "pv_evil2", pr_url: "http://github.com/o/r/pull/1" }),
      versionRow({ version_id: "pv_evil3", pr_url: "javascript:alert(1)" }),
    ];
    api.get.mockResolvedValue({ data: { ok: true, versions, total: 3, locked_count: 0 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("changes-list")).toBeInTheDocument());

    expect(screen.queryByTestId("changes-pr-link-pv_evil1")).toBeNull();
    expect(screen.queryByTestId("changes-pr-link-pv_evil2")).toBeNull();
    expect(screen.queryByTestId("changes-pr-link-pv_evil3")).toBeNull();
  });

  it("shows an honest 'not available' state on a 404 (flag off)", async () => {
    api.get.mockRejectedValue({ response: { status: 404 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("changes-not-available")).toBeInTheDocument());
  });

  it("marks a rolled-back version distinctly", async () => {
    const versions = [versionRow({ version_id: "pv_1", status: "rolled_back" })];
    api.get.mockResolvedValue({ data: { ok: true, versions, total: 1, locked_count: 0 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("changes-rolled-back-pv_1")).toBeInTheDocument());
  });
});
