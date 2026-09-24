/**
 * AdminCockpit.stripeModeBoard.test.jsx — 2026-09-05 admin-panel audit fix.
 *
 * Bug: ModeBoard's stripe chip read the binary `/admin/status/all`
 * int_stripe check (green=configured / anything else="not-set"),
 * which collapsed a "warn" status (Stripe key present, test mode,
 * charges disabled) into the SAME "not-set" label as a genuinely
 * missing integration — disagreeing with /admin/integrations, which
 * correctly showed "DEGRADED · TEST mode". Fix: read the richer
 * per-integration status from /admin/integrations/health instead.
 *
 * Named tests:
 *   t_stripe_warn_shows_test_mode_not_not_set
 *   t_stripe_missing_shows_not_set
 *   t_stripe_ok_shows_configured
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const get = vi.fn();
vi.mock("../../lib/api", () => ({ api: { get: (...args) => get(...args) } }));

import { ModeBoard } from "../AdminCockpit.jsx";

const renderPage = () => render(<MemoryRouter><ModeBoard /></MemoryRouter>);

function mockCommon(stripeResult) {
  get.mockImplementation((path) => {
    if (path === "/version") {
      return Promise.resolve({ data: { environment: "production", commit_sha: "abc1234" } });
    }
    if (path === "/admin/integrations/health") {
      return Promise.resolve({ data: { results: stripeResult ? [stripeResult] : [] } });
    }
    if (path === "/admin/status/all") {
      return Promise.resolve({ data: { checks: [], counts: { green: 0, red: 0, gray: 0, total: 0, health_pct: 0 } } });
    }
    return Promise.reject(new Error("not mocked in this test: " + path));
  });
}

beforeEach(() => {
  get.mockReset();
});

describe("AdminCockpit — ModeBoard stripe chip (2026-09-05 audit fix)", () => {
  it("t_stripe_warn_shows_test_mode_not_not_set: warn status -> 'test mode', not 'not-set'", async () => {
    mockCommon({ id: "stripe", name: "Stripe", status: "warn", summary: "TEST mode (charges DISABLED)" });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("cockpit-mode-board")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("cockpit-mode-board").textContent).toMatch(/stripe: test mode/));
    expect(screen.getByTestId("cockpit-mode-board").textContent).not.toMatch(/stripe: not-set/);
  });

  it("t_stripe_missing_shows_not_set: missing status -> 'not-set'", async () => {
    mockCommon({ id: "stripe", name: "Stripe", status: "missing", summary: "missing env" });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("cockpit-mode-board").textContent).toMatch(/stripe: not-set/));
  });

  it("t_stripe_ok_shows_configured: ok status -> 'configured'", async () => {
    mockCommon({ id: "stripe", name: "Stripe", status: "ok", summary: "LIVE mode" });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("cockpit-mode-board").textContent).toMatch(/stripe: configured/));
  });
});
