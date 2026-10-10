/**
 * Admin shell — the Owner's first render after GET /admin/me succeeds.
 *
 * Bug: /admin would not open. ADMIN_NAV listed "financials" but the sidebar's
 * icon map had no entry for it, so the sidebar rendered `<undefined />`
 * ("Element type is invalid") and React unmounted the whole console. A bad
 * merge also left 13 nav ids listed twice (duplicate React keys).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const get = vi.fn();
vi.mock("../../lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    api: { ...real.api, get: (...a) => get(...a), post: vi.fn(() => Promise.resolve({ data: {} })) },
  };
});

// vitest.config.js has no React plugin (classic JSX), and some widgets the
// cockpit mounts (e.g. ChurnRiskWidget) do not import React themselves.
globalThis.React = React;

import Admin from "../Admin.jsx";
import { ADMIN_NAV } from "../../lib/adminNav";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q) => ({
    matches: false, media: q, onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent() { return false; },
  }));
  get.mockReset();
  get.mockImplementation((path) => {
    if (path === "/admin/me") return Promise.resolve({ data: { email: "owner@example.test", user_id: "u1", is_admin: true } });
    if (path === "/admin/team/my-access") return Promise.resolve({ data: { is_owner: true, role: "owner", screens: {} } });
    return new Promise(() => {});
  });
  localStorage.clear();
  localStorage.setItem("aurem_token", "t");
  localStorage.setItem("aurem_user", JSON.stringify({ email: "owner@example.test", is_admin: true, tier: "founder" }));
});

describe("Admin shell for the Owner", () => {
  it("renders every sidebar entry once, without a React error", async () => {
    const errors = [];
    vi.spyOn(console, "error").mockImplementation((...a) => errors.push(a.map(String).join(" ")));
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes><Route path="/admin" element={<Admin initialTab="cockpit" />} /></Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("Financials")).toBeTruthy());
    for (const item of ADMIN_NAV) {
      expect(screen.getAllByText(item.label).length).toBeGreaterThan(0);
    }
    expect(errors.filter((e) => /Element type is invalid|same key/.test(e))).toEqual([]);
  });
});
