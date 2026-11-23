/**
 * api.healthApi.test.js - 2026-11-22 - app-mount regression
 * (fix/app-mount-stuck-2026-11-22)
 *
 * healthApi was created at module scope with baseURL `${BACKEND}/api`,
 * but BACKEND is not defined anywhere - importing lib/api.js threw
 * `ReferenceError: BACKEND is not defined`, the entry bundle never
 * evaluated, and the app stayed on the loading splash forever.
 * Pin the relative base here so it cannot regress.
 */
import { describe, it, expect } from "vitest";
import { healthApi } from "../api";

describe("api.js - healthApi same-origin relative base", () => {
  it("healthApi uses the relative /api base", () => {
    expect(healthApi.defaults.baseURL).toBe("/api");
  });

  it("healthApi keeps its 10s timeout", () => {
    expect(healthApi.defaults.timeout).toBe(10000);
  });

  it("healthApi base never contains an absolute origin", () => {
    expect(healthApi.defaults.baseURL.startsWith("http")).toBe(false);
  });
});
