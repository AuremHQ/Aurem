/**
 * VisibilityKitPanel.github_disconnected_2026_09_20.test.jsx
 *
 * Live bug repro (reported while testing the Kit against a real
 * connected customer project, TJSNDHU/Aurem, used only as the test
 * subject — the bug is in Aurem's own panel, not that repo's code).
 *
 * Symptom: the panel showed every "auto" item as a green "Ready"
 * chip with an enabled "Apply Visibility Kit (N)" CTA even for a
 * project whose GitHub App auth was actually disconnected. Clicking
 * Apply hit a 403 from the backend's own auth check
 * (services/pat_vault.py::get_repo_token_or_error) that surfaced only
 * as a brief toast — the panel's score/chips never explained why,
 * reading exactly like "the Kit is broken" instead of "this project
 * needs to be reconnected".
 *
 * Root cause: `apply_enabled` from `GET /visibility/projects/{id}/
 * state` only ever reflected the platform-wide R9 kill-switch
 * (`kit_apply_enabled`) — never this SPECIFIC project's own GitHub
 * connection health. The panel never checked
 * `GET /cto/projects/connection-status` (the exact endpoint
 * ProjectSwitcher already uses for this) before offering Apply as
 * actionable.
 *
 * Fix: VisibilityKitPanel now fetches connection-status on mount and
 * combines it with the R9 flag — a "disconnected" project disables
 * both the main CTA and every per-row Apply button, with a clear,
 * persistent banner (not just a toast) explaining why. A transient
 * "unreachable" status does NOT block Apply (same lesson
 * ProjectSwitcher already learned about over-reacting to blips).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const get = vi.fn();
const post = vi.fn();
vi.mock("../../lib/api", () => ({ api: { get: (...a) => get(...a), post: (...a) => post(...a) } }));

const rawToast = vi.fn();
vi.mock("../Toast", () => ({ toast: (...a) => rawToast(...a) }));

import VisibilityKitPanel from "../VisibilityKitPanel.jsx";

const STATE_READY = {
  apply_enabled: true,
  apply_disabled_reason: null,
  score: 0,
  items: [
    { key: "ai_crawler_policy", name: "robots.txt AI policy", mode: "auto", status: "missing", what_why: "x" },
  ],
};

function mockGetByUrl(map) {
  get.mockImplementation((url) => {
    for (const [pattern, resp] of Object.entries(map)) {
      if (url.includes(pattern)) return Promise.resolve({ data: resp });
    }
    return Promise.resolve({ data: {} });
  });
}

beforeEach(() => { get.mockReset(); post.mockReset(); rawToast.mockReset(); });

describe("VisibilityKitPanel — GitHub disconnection gate (2026-09-20 fix)", () => {
  it("disables the main Apply CTA and shows a persistent banner when the project is disconnected", async () => {
    mockGetByUrl({
      "/visibility/projects/": STATE_READY,
      "/cto/projects/connection-status": {
        statuses: [{ project_id: "p_test", status: "disconnected" }],
      },
    });
    render(<VisibilityKitPanel projectId="p_test" siteDomain="example.com" onClose={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("kit-apply-disabled-banner")).toBeInTheDocument());
    expect(screen.getByTestId("kit-apply-disabled-banner").textContent).toMatch(/disconnected from GitHub/i);

    const cta = screen.getByTestId("kit-apply-all-cta");
    expect(cta).toBeDisabled();
  });

  it("does NOT block Apply for a merely 'unreachable' (transient) status", async () => {
    mockGetByUrl({
      "/visibility/projects/": STATE_READY,
      "/cto/projects/connection-status": {
        statuses: [{ project_id: "p_test", status: "unreachable" }],
      },
    });
    render(<VisibilityKitPanel projectId="p_test" siteDomain="example.com" onClose={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("kit-apply-all-cta")).toBeInTheDocument());
    expect(screen.queryByTestId("kit-apply-disabled-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("kit-apply-all-cta")).not.toBeDisabled();
  });

  it("leaves Apply enabled when the project IS connected (no false positives)", async () => {
    mockGetByUrl({
      "/visibility/projects/": STATE_READY,
      "/cto/projects/connection-status": {
        statuses: [{ project_id: "p_test", status: "connected" }],
      },
    });
    render(<VisibilityKitPanel projectId="p_test" siteDomain="example.com" onClose={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("kit-apply-all-cta")).toBeInTheDocument());
    expect(screen.queryByTestId("kit-apply-disabled-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("kit-apply-all-cta")).not.toBeDisabled();
  });

  it("still respects the R9 platform-wide flag independently of connection status", async () => {
    mockGetByUrl({
      "/visibility/projects/": { ...STATE_READY, apply_enabled: false, apply_disabled_reason: "Apply available once ship-via-PR (R9) is proven live on production." },
      "/cto/projects/connection-status": {
        statuses: [{ project_id: "p_test", status: "connected" }],
      },
    });
    render(<VisibilityKitPanel projectId="p_test" siteDomain="example.com" onClose={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("kit-apply-disabled-banner")).toBeInTheDocument());
    expect(screen.getByTestId("kit-apply-disabled-banner").textContent).toMatch(/R9/);
    expect(screen.getByTestId("kit-apply-all-cta")).toBeDisabled();
  });
});
