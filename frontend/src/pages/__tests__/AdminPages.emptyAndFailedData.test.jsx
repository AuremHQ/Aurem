/**
 * Admin pages must survive empty / partial API data and failed loads.
 *
 * 2026-09-29 admin audit: with `{}` API responses, Cockpit (the default
 * /admin landing page), Architecture, Financials, Funnel, Onboarding
 * funnel, QA Health, System Health and System Map all threw while
 * rendering and fell into the route error boundary; Maintenance did the
 * same after a failed load. Realistic triggers: a fresh database, an older
 * backend, or a degraded dependency returning a partial payload.
 *
 * Each page is rendered with (a) `{}` for every call and (b) every call
 * failing with HTTP 500, and must neither throw nor log a React render
 * error.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Several page modules use JSX without importing React (the app builds them
// with the automatic runtime; vitest here uses the classic one).
globalThis.React = React;

let mode = "empty"; // "empty" | "fail"
// A real Error (not a bare object) so an unhandled rejection prints a stack
// that names the component that forgot to catch the failed call.
const failure = () => {
  const e = new Error("Request failed with status code 500");
  e.response = { status: 500, data: { detail: "boom" } };
  return Promise.reject(e);
};
const respond = () =>
  mode === "fail" ? failure() : Promise.resolve({ data: {}, status: 200 });

vi.mock("../../lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    api: {
      ...real.api,
      get: () => respond(),
      post: () => respond(),
      put: () => respond(),
      patch: () => respond(),
      delete: () => respond(),
    },
  };
});
// AdminQADashboard calls axios.get/post directly (not the shared `api`
// instance); keep the real library so lib/api.js can still axios.create().
vi.mock("axios", async (importOriginal) => {
  const real = await importOriginal();
  const fn = () => respond();
  Object.assign(real.default, { get: fn, post: fn, put: fn, patch: fn, delete: fn });
  return real;
});

import AdminCockpit from "../AdminCockpit.jsx";
import AdminArchitecturePage from "../AdminArchitecturePage.jsx";
import AdminFinancials from "../AdminFinancials.jsx";
import AdminFunnelPage from "../AdminFunnelPage.jsx";
import AdminOnboardingFunnel from "../AdminOnboardingFunnel.jsx";
import AdminQADashboard from "../AdminQADashboard.jsx";
import AdminSystemHealth from "../AdminSystemHealth.jsx";
import FeatureWindow from "../FeatureWindow.jsx";
import AdminMaintenance from "../AdminMaintenance.jsx";
import ChurnRiskWidget from "../../components/ChurnRiskWidget.jsx";
import DuplicationWidget from "../../components/DuplicationWidget.jsx";

const PAGES = [
  ["AdminCockpit (/admin)", AdminCockpit],
  ["AdminArchitecturePage", AdminArchitecturePage],
  ["AdminFinancials", AdminFinancials],
  ["AdminFunnelPage", AdminFunnelPage],
  ["AdminOnboardingFunnel", AdminOnboardingFunnel],
  ["AdminQADashboard", AdminQADashboard],
  ["AdminSystemHealth", AdminSystemHealth],
  ["FeatureWindow (system map)", FeatureWindow],
  ["AdminMaintenance", AdminMaintenance],
];

let errorSpy;
beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q) => ({
    matches: false, media: q, onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent() { return false; },
  }));
  globalThis.ResizeObserver = globalThis.ResizeObserver || class {
    observe() {} unobserve() {} disconnect() {}
  };
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") }));
  localStorage.setItem("aurem_token", "t");
  localStorage.setItem("aurem_user", JSON.stringify({ is_admin: true, is_founder: true }));
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  errorSpy.mockRestore();
  localStorage.clear();
});

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)); });
const renderErrors = () =>
  errorSpy.mock.calls
    .map((c) => String(c[0]) + " " + String(c[1] ?? ""))
    .filter((m) => /Cannot read properties|is not a function|is not iterable|Cannot convert undefined|The above error occurred/.test(m));

describe.each([["empty", "empty `{}` API responses"], ["fail", "every API call failing with HTTP 500"]])(
  "admin pages with %s data",
  (m, label) => {
    it.each(PAGES)(`%s renders without crashing (${label})`, async (_name, Page) => {
      mode = m;
      expect(() => {
        render(<MemoryRouter><Page /></MemoryRouter>);
      }).not.toThrow();
      await settle();
      expect(renderErrors()).toEqual([]);
    });
  },
);

describe("a failed load shows an error and leaves no unhandled rejection", () => {
  // Vitest fails the whole run on an unhandled rejection, so these tests also
  // guard against a widget forgetting to catch the failed call (the Cockpit's
  // Churn and Duplication widgets and the Onboarding funnel each did).
  it("ChurnRiskWidget shows its error message", async () => {
    mode = "fail";
    render(<MemoryRouter><ChurnRiskWidget /></MemoryRouter>);
    expect(await screen.findByTestId("churn-risk-error")).toBeTruthy();
  });

  it("DuplicationWidget shows its error message", async () => {
    mode = "fail";
    render(<MemoryRouter><DuplicationWidget /></MemoryRouter>);
    expect(await screen.findByTestId("duplication-error")).toBeTruthy();
  });

  it("Onboarding funnel shows an error with a Retry button", async () => {
    mode = "fail";
    render(<MemoryRouter><AdminOnboardingFunnel /></MemoryRouter>);
    expect(await screen.findByTestId("admin-onboarding-funnel-error")).toBeTruthy();
    expect(screen.getByTestId("admin-onboarding-funnel-retry")).toBeTruthy();
    expect(await screen.findByTestId("admin-onboarding-trend-error")).toBeTruthy();
  });
});
