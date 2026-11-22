/**
 * api.relativeBase.test.js — 2026-11-21 · Aurem Deploy migration
 *
 * The frontend must call the backend same-origin: ONE build runs on
 * staging and production, so no build-time backend URL may be baked
 * into the bundle any more. lib/api.js is the single source for the
 * API base — every other module imports API_BASE from it (or uses a
 * plain relative path), so asserting it here pins the whole migration.
 */
import { describe, it, expect } from "vitest";
import { API_BASE, api } from "../api";

describe("api.js — same-origin relative API base (Aurem Deploy)", () => {
  it("API_BASE is the relative /api/aurem-dev path, not a baked origin", () => {
    expect(API_BASE).toBe("/api/aurem-dev");
  });

  it("the axios instance uses the same relative baseURL", () => {
    expect(api.defaults.baseURL).toBe("/api/aurem-dev");
  });

  it("the base never contains an absolute origin", () => {
    expect(API_BASE.startsWith("http")).toBe(false);
    expect(API_BASE.startsWith("/api/")).toBe(true);
  });
});
