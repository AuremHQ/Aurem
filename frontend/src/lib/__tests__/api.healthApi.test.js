/**
 * api.healthApi.test.js - 2026-11-23 - app-mount fix.
 * Regression: healthApi used a baseURL with BACKEND undefined,
 * so importing api.js threw ReferenceError and the app never
 * mounted (stuck on the loading splash).
 */
import { describe, it, expect } from "vitest";
import { healthApi } from "../api";

describe("healthApi (same-origin relative base URL)", () => {
  it("the health client uses the relative /api base URL", () => {
    expect(healthApi.defaults.baseURL).toBe("/api");
  });

  it("the base never contains an absolute origin", () => {
    expect(healthApi.defaults.baseURL.startsWith("http")).toBe(false);
    expect(healthApi.defaults.baseURL.startsWith("/api")).toBe(true);
  });

  it("the health client keeps its 10s timeout", () => {
    expect(healthApi.defaults.timeout).toBe(10000);
  });
});
