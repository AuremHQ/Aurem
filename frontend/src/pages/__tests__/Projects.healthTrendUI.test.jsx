/**
 * Projects.healthTrendUI.test.jsx — Ticket E-UI (2026-02) behavioral
 * RTL tests: the per-row sparkline (sidebar list) and the full trend
 * chart now also living in the project detail view (not only inside
 * Edit). Explicit "no fake data" contract: nothing renders until real
 * history (>=2 scored snapshots) exists.
 */
import React from "react";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { beforeEach, afterEach, it, expect, vi, describe } from "vitest";

vi.mock("../../components/Shell", () => ({ default: ({ children }) => <>{children}</>, PageHeader: () => null }));
vi.mock("../../components/nav/RailShell", () => ({ default: ({ children }) => <>{children}</> }));
vi.mock("../../components/FounderOfferPill", () => ({ default: () => null }));
vi.mock("../../components/ArchitectBlueprintPanel", () => ({ default: () => null }));
vi.mock("../../components/Toast", () => ({ toast: vi.fn() }));
vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), patch: vi.fn(), post: vi.fn() }, getToken: () => "test", API_BASE: "https://api.test" }));
vi.mock("../../components/AddProjectWizard", () => ({ default: () => null }));

import Projects from "../Projects";
import { api } from "../../lib/api";

const proj = { project_id: "p_1", name: "Demo Project", github_owner: "team", github_repo: "demo" };

function mockApiGet({ snapshotsByProject = {} } = {}) {
  api.get.mockImplementation((url, cfg) => {
    if (url === "/codebase-health/trend") {
      const pid = cfg?.params?.project_id;
      return Promise.resolve({ data: { ok: true, snapshots: snapshotsByProject[pid] || [] } });
    }
    return Promise.resolve({ data: { projects: [proj], tasks: [] } });
  });
}

afterEach(() => window.history.replaceState({}, "", "/"));

describe("Ticket E-UI · project row sparkline", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders NO sparkline at all when there is no real history (no fake data)", async () => {
    mockApiGet({ snapshotsByProject: { p_1: [] } });
    render(<Projects />);
    await screen.findByTestId("proj-row-p_1");
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(
      "/codebase-health/trend", { params: { project_id: "p_1" } },
    ));
    expect(screen.queryByTestId("proj-row-sparkline-p_1")).toBeNull();
  });

  it("renders NO sparkline with only a single real snapshot (need >=2 points for a trend)", async () => {
    mockApiGet({ snapshotsByProject: { p_1: [{ score: 80, snapshot_at: 1000 }] } });
    render(<Projects />);
    await screen.findByTestId("proj-row-p_1");
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(screen.queryByTestId("proj-row-sparkline-p_1")).toBeNull();
  });

  it("renders a real sparkline with an accessible label once >=2 real snapshots exist", async () => {
    mockApiGet({
      snapshotsByProject: {
        p_1: [{ score: 60, snapshot_at: 1000 }, { score: 85, snapshot_at: 2000 }],
      },
    });
    render(<Projects />);
    await screen.findByTestId("proj-row-p_1");
    await waitFor(() => expect(screen.getByTestId("proj-row-sparkline-p_1")).toBeInTheDocument());
    const spark = screen.getByTestId("proj-row-sparkline-p_1");
    expect(spark).toHaveAttribute("role", "img");
    expect(spark).toHaveAttribute("aria-label", "Health score trend, latest score 85");
  });
});

describe("Ticket E-UI · full trend chart in project detail view", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the full chart card in the detail view (not only inside Edit) once a project is selected", async () => {
    mockApiGet({
      snapshotsByProject: {
        p_1: [{ score: 60, snapshot_at: 1000 }, { score: 85, snapshot_at: 2000 }],
      },
    });
    render(<Projects />);
    const row = await screen.findByTestId("proj-row-p_1");
    fireEvent.click(row);
    await waitFor(() => expect(screen.getByTestId("proj-detail-health-trend")).toBeInTheDocument());
    const card = screen.getByTestId("proj-detail-health-trend");
    await waitFor(() => expect(within(card).getByTestId("proj-health-trend-chart")).toBeInTheDocument());
  });

  it("shows the real 'No history yet.' empty state in the detail view, never a fake chart", async () => {
    mockApiGet({ snapshotsByProject: { p_1: [] } });
    render(<Projects />);
    const row = await screen.findByTestId("proj-row-p_1");
    fireEvent.click(row);
    await waitFor(() => expect(screen.getByTestId("proj-detail-health-trend")).toBeInTheDocument());
    const card = screen.getByTestId("proj-detail-health-trend");
    await waitFor(() => expect(within(card).getByTestId("proj-health-trend-empty")).toBeInTheDocument());
    expect(within(card).getByTestId("proj-health-trend-empty")).toHaveTextContent("No history yet.");
    expect(within(card).queryByTestId("proj-health-trend-chart")).toBeNull();
  });
});

describe("Ticket C-UI compliance fix · Download PDF lives on the Projects page itself", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the Download PDF button in the project detail view, not only on /codebase-health", async () => {
    mockApiGet({ snapshotsByProject: { p_1: [] } });
    render(<Projects />);
    const row = await screen.findByTestId("proj-row-p_1");
    fireEvent.click(row);
    await waitFor(() => expect(screen.getByTestId("proj-detail-health-trend")).toBeInTheDocument());
    const card = screen.getByTestId("proj-detail-health-trend");
    expect(within(card).getByTestId("health-pdf-download-btn")).toBeInTheDocument();
    expect(within(card).getByTestId("health-pdf-download-btn")).toHaveTextContent("Download PDF");
  });
});
