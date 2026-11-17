/**
 * AdminHarnessOverview.test.jsx — /admin/harness side-panel section.
 *
 * Covers: real signups/downloads/telemetry data rendering (no
 * hardcoded values), the harness_homepage-only filter toggle
 * re-querying with the source param, and that Releases links to the
 * EXISTING /admin/harness-releases page rather than duplicating it.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn() },
}));

import AdminHarnessOverview from "../AdminHarnessOverview.jsx";
import { api } from "../../lib/api";

function mockEndpoints({ signups = [], harnessCount = 0, downloads = { downloads: [], total: 0, free_count: 0, paid_count: 0 }, telemetry = { endpoint: "/api/harness/telemetry", total_events: 0, last_received_at: null } }) {
  api.get.mockImplementation((path, config) => {
    if (path === "/admin/harness/signups") {
      const filtered = config?.params?.source
        ? signups.filter((s) => s.signup_source === config.params.source)
        : signups;
      return Promise.resolve({ data: { signups: filtered, total: filtered.length, harness_homepage_count: harnessCount } });
    }
    if (path === "/admin/harness/downloads") {
      return Promise.resolve({ data: downloads });
    }
    if (path === "/admin/harness/telemetry-status") {
      return Promise.resolve({ data: telemetry });
    }
    return Promise.reject(new Error(`unexpected path ${path}`));
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/harness"]}>
      <Routes>
        <Route path="/admin/harness" element={<AdminHarnessOverview />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AdminHarnessOverview", () => {
  it("renders real signups, downloads, and telemetry data — nothing hardcoded", async () => {
    mockEndpoints({
      signups: [
        { email: "a@example.com", created_at: 1763251200, signup_source: "harness_homepage" },
        { email: "b@example.com", created_at: 1763251300, signup_source: null },
      ],
      harnessCount: 1,
      downloads: { downloads: [{ grant_id: "dl_1", version: "0.8.0", plan: "free", issued_at: 1763251400 }], total: 1, free_count: 1, paid_count: 0 },
      telemetry: { endpoint: "/api/harness/telemetry", total_events: 5, last_received_at: "2026-11-16T12:00:00+00:00" },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-overview-signups-list")).toBeInTheDocument());
    expect(screen.getByText("a@example.com")).toBeInTheDocument();
    expect(screen.getByText("b@example.com")).toBeInTheDocument();
    expect(screen.getByText("harness_homepage")).toBeInTheDocument();
    expect(screen.getByTestId("harness-overview-downloads-total")).toHaveTextContent("1");
    expect(screen.getByTestId("harness-overview-telemetry-status")).toHaveTextContent("5");
  });

  it("toggling the harness_homepage-only filter re-queries with the source param", async () => {
    mockEndpoints({
      signups: [
        { email: "a@example.com", created_at: 1763251200, signup_source: "harness_homepage" },
        { email: "b@example.com", created_at: 1763251300, signup_source: null },
      ],
      harnessCount: 1,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("b@example.com")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("harness-overview-signups-filter"));

    await waitFor(() => expect(api.get).toHaveBeenCalledWith(
      "/admin/harness/signups", { params: { source: "harness_homepage" } },
    ));
    await waitFor(() => expect(screen.queryByText("b@example.com")).not.toBeInTheDocument());
    expect(screen.getByText("a@example.com")).toBeInTheDocument();
  });

  it("links Releases to the existing /admin/harness-releases page instead of duplicating it", async () => {
    mockEndpoints({});
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-overview-releases-link")).toBeInTheDocument());
    expect(screen.getByTestId("harness-overview-releases-link")).toHaveAttribute("href", "/admin/harness-releases");
  });

  it("shows zero-state text instead of crashing when there's no data yet", async () => {
    mockEndpoints({});
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-overview-signups-list")).toBeInTheDocument());
    expect(screen.getByText("No signups yet.")).toBeInTheDocument();
    expect(screen.getByText("No downloads yet.")).toBeInTheDocument();
    expect(screen.getByTestId("harness-overview-downloads-total")).toHaveTextContent("0");
  });
});
