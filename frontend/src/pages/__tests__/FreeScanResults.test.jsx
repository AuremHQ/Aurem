/**
 * FreeScanResults.test.jsx — Master Prompt v2 PART 4.1/4.4.
 * "free scan results page shows all issues" + stored-XSS-from-repo-
 * strings safety (React text rendering, no dangerouslySetInnerHTML).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import FreeScanResults from "../FreeScanResults.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPage(projectId = "proj1") {
  return render(
    <MemoryRouter initialEntries={[`/projects/${projectId}/free-scan`]}>
      <Routes>
        <Route path="/projects/:projectId/free-scan" element={<FreeScanResults />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FreeScanResults", () => {
  it("shows the scanning banner while status is scanning", async () => {
    api.get.mockResolvedValue({ data: { status: "scanning" } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("free-scan-banner-scanning")).toBeInTheDocument());
  });

  it("shows the real score and ALL issues once ready", async () => {
    api.get.mockResolvedValue({
      data: {
        status: "ready", score: 41,
        share_card: { score: 41 },
        categories_run: ["security"], categories_not_run: [],
        issues: [
          { name: "sql_injection", severity: "critical", file: "a.py", line: 1 },
          { name: "missing_alt_text", severity: "low", file: "b.html", line: 2 },
          { name: "todo_comment", severity: "info", file: "c.js", line: 3 },
        ],
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("free-scan-results")).toBeInTheDocument());
    expect(screen.getByTestId("free-scan-score")).toHaveTextContent("Your score: 41");
    const rows = screen.getAllByTestId("free-scan-issue-row");
    expect(rows).toHaveLength(3);
    expect(screen.getByTestId("free-scan-share-card")).toHaveTextContent("My score: 41");
  });

  it("share card never includes issue names or file paths", async () => {
    api.get.mockResolvedValue({
      data: {
        status: "ready", score: 10, share_card: { score: 10 },
        issues: [{ name: "secret_leaked_path/credentials.env", severity: "critical", file: "secret_leaked_path/credentials.env" }],
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("free-scan-share-card")).toBeInTheDocument());
    expect(screen.getByTestId("free-scan-share-card").textContent).not.toContain("credentials.env");
  });

  it("renders a malicious repo-derived file name as plain text, not HTML (stored-XSS safety)", async () => {
    const malicious = "<img src=x onerror=alert(1)>.py";
    api.get.mockResolvedValue({
      data: {
        status: "ready", score: 5, share_card: { score: 5 },
        issues: [{ name: "rule1", severity: "high", file: malicious, line: 1 }],
      },
    });
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByTestId("free-scan-issue-row")).toBeInTheDocument());
    // React renders this as a TEXT node — no actual <img> element is created.
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByTestId("free-scan-issue-file").textContent).toBe(malicious);
  });

  it("shows categories_not_run when the cost cap truncated the scan", async () => {
    api.get.mockResolvedValue({
      data: { status: "ready", score: 50, share_card: { score: 50 }, issues: [], categories_not_run: ["bug_hunt", "docker"] },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("free-scan-categories-not-run")).toBeInTheDocument());
  });

  it("shows a not-found state for a 404 (project not owned / nonexistent)", async () => {
    api.get.mockRejectedValue({ response: { status: 404 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("free-scan-not-found")).toBeInTheDocument());
  });
});
