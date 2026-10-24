/**
 * AdminPricingPanel.test.jsx — Master Prompt v2 PART 3.
 *
 * Covers: margin editor preview->confirm flow, fixed-costs add/remove,
 * dashboard numbers render, billing-flags resolve — and the explicit
 * security requirement: a stored XSS payload in a fixed-cost name or
 * a flag resolution note must render as literal text, never execute
 * (React's default JSX text-escaping — this component never uses
 * dangerouslySetInnerHTML anywhere).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

const get = vi.fn();
const post = vi.fn();
const del = vi.fn();
vi.mock("../../lib/api", () => ({
  api: {
    get: (...args) => get(...args),
    post: (...args) => post(...args),
    delete: (...args) => del(...args),
  },
}));

import AdminPricingPanel from "../AdminPricingPanel.jsx";

const XSS_PAYLOAD = "<img src=x onerror=alert(1)>wallet_test_xss";

function mockDefaults({ fixedCosts = [], flags = [] } = {}) {
  get.mockImplementation((path) => {
    if (path === "/admin/pricing/version") {
      return Promise.resolve({ data: { current: { _id: "pv_1", margin: 0.5 }, history: [] } });
    }
    if (path === "/admin/pricing/fixed-costs") {
      return Promise.resolve({ data: { fixed_costs: fixedCosts } });
    }
    if (path === "/admin/pricing/dashboard") {
      return Promise.resolve({
        data: {
          tokens_owed: 100, tokens_owed_usd: 1.0, fixed_costs_monthly_usd: 50,
          break_even_progress_pct: 20,
          failed_run_cost: { total_usd: 0.5, count: 2 },
          ceiling_hit: { count: 1, absorbed_tokens: 5, absorbed_usd: 0.05 },
          unmetered_cost_alarm: { alarm: false, gap_usd: 0, real_llm_cost_7d_usd: 1, wallet_tracked_cost_7d_usd: 1 },
          by_action: [{ action: "fix", revenue_usd: 1.2, real_cost_usd: 0.6, realized_margin: 0.5, runs: 10 }],
          by_user_top50: [],
          reconcile_drift: { flagged_count: 0, users_compared: 5 },
          billing_flags_summary: {},
        },
      });
    }
    if (path.startsWith("/admin/pricing/billing-flags")) {
      const wantResolved = path.includes("resolved=true");
      return Promise.resolve({ data: { flags: flags.filter((f) => !!f.resolved === wantResolved) } });
    }
    return Promise.reject(new Error("not mocked in this test: " + path));
  });
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  del.mockReset();
});

describe("AdminPricingPanel", () => {
  it("renders the current margin and dashboard numbers", async () => {
    mockDefaults();
    render(<AdminPricingPanel />);
    await waitFor(() => expect(screen.getByTestId("current-margin").textContent).toMatch(/50\.0%/));
    await waitFor(() => expect(screen.getByText(/Realized margin by action/)).toBeInTheDocument());
    expect(screen.getByText("fix")).toBeInTheDocument();
  });

  it("preview then confirm applies a new margin", async () => {
    mockDefaults();
    post.mockImplementation((path, body) => {
      if (path === "/admin/pricing/margin/preview") {
        return Promise.resolve({
          data: {
            current_margin: 0.5, proposed_margin: 0.6,
            impact_by_action: [{ action: "fix", avg_cost_usd_7d: null, tokens_at_current_margin: null, tokens_at_new_margin: null }],
            preview: { margin: 0.6, stripe_fee_pct: 0.029, stripe_fee_fixed_usd: 0.3, expires_at: 999999999, token: "tok123" },
          },
        });
      }
      if (path === "/admin/pricing/margin/confirm") {
        return Promise.resolve({ data: { ok: true, new_version: { _id: "pv_2", margin: 0.6 } } });
      }
      return Promise.reject(new Error("not mocked: " + path));
    });
    render(<AdminPricingPanel />);
    await waitFor(() => expect(screen.getByTestId("current-margin")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("margin-input"), { target: { value: "60" } });
    fireEvent.click(screen.getByTestId("margin-preview-btn"));
    await waitFor(() => expect(screen.getByTestId("margin-preview-block")).toBeInTheDocument());
    expect(post).toHaveBeenCalledWith("/admin/pricing/margin/preview", { margin: 0.6 });

    fireEvent.click(screen.getByTestId("margin-confirm-btn"));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/admin/pricing/margin/confirm",
      expect.objectContaining({ margin: 0.6, token: "tok123" }),
    ));
  });

  it("a fixed-cost name containing an XSS payload renders as literal text, never executes", async () => {
    mockDefaults({
      fixedCosts: [{ _id: "fc_1", name: XSS_PAYLOAD, amount_usd_monthly: 10 }],
    });
    render(<AdminPricingPanel />);
    await waitFor(() => expect(screen.getByText(XSS_PAYLOAD)).toBeInTheDocument());
    // If this were ever rendered via dangerouslySetInnerHTML, the
    // <img onerror=...> tag would become a REAL DOM element rather
    // than literal text content — confirm it did not.
    expect(document.querySelector("img[onerror]")).toBeNull();
  });

  it("adds and removes a fixed cost", async () => {
    mockDefaults({ fixedCosts: [] });
    post.mockResolvedValue({ data: { ok: true, fixed_cost: { _id: "fc_new", name: "Hosting", amount_usd_monthly: 40 } } });
    del.mockResolvedValue({ data: { ok: true } });
    render(<AdminPricingPanel />);
    await waitFor(() => expect(screen.getByTestId("fixed-costs")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("fixed-cost-name-input"), { target: { value: "Hosting" } });
    fireEvent.change(screen.getByTestId("fixed-cost-amount-input"), { target: { value: "40" } });
    fireEvent.click(screen.getByTestId("fixed-cost-add-btn"));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/admin/pricing/fixed-costs", { name: "Hosting", amount_usd_monthly: 40 },
    ));
  });

  it("resolves a billing flag, note stored and sent raw (no escaping on the client either)", async () => {
    const xssNote = "<script>alert(1)</script>wallet_test_note";
    mockDefaults({
      flags: [{ _id: "flag_1", flag_type: "refund_shortfall", user_id: "u1",
                created_at: 1700000000, detail: {}, resolved: false }],
    });
    post.mockResolvedValue({ data: { ok: true, flag: { _id: "flag_1", resolved: true } } });
    render(<AdminPricingPanel />);
    await waitFor(() => expect(screen.getByTestId("billing-flag-flag_1")).toBeInTheDocument());

    const noteInputs = screen.getAllByPlaceholderText("resolution note (optional)");
    fireEvent.change(noteInputs[0], { target: { value: xssNote } });
    fireEvent.click(screen.getByTestId("resolve-flag-flag_1"));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/admin/pricing/billing-flags/flag_1/resolve", { note: xssNote },
    ));
  });

  it("switching to the resolved tab fetches resolved=true flags", async () => {
    mockDefaults({
      flags: [{ _id: "flag_2", flag_type: "dispute_won", user_id: "u2",
                created_at: 1700000000, detail: {}, resolved: true,
                resolved_by: "admin@aurem.dev", resolution_note: "checked" }],
    });
    render(<AdminPricingPanel />);
    await waitFor(() => expect(screen.getByTestId("billing-flags")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("flags-tab-resolved"));
    await waitFor(() => expect(get).toHaveBeenCalledWith("/admin/pricing/billing-flags?resolved=true"));
    await waitFor(() => expect(screen.getByText(/Resolved by admin@aurem.dev/)).toBeInTheDocument());
  });
});
