/**
 * api.dedupedGetParams.oraAuditP0.test.js — ORA audit P0 (claim #5).
 *
 * dedupedGet() used to key its 1500 ms / 2000 ms coalescing cache on
 * the raw url only, discarding config.params (which axios serialises
 * into the query string at request time). Two calls to the same url
 * with different params shared one entry, so the second caller got
 * the first caller's response. The fix folds serialised params into
 * the cache key.
 *
 * axios is mocked at the module boundary (the cache wraps the bound
 * axios instance method), so no network is involved. Distinct task
 * ids per test keep the module-level cache from leaking between
 * cases; every test runs well inside the TTLs.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const { axiosGet } = vi.hoisted(() => ({ axiosGet: vi.fn() }));

vi.mock("axios", () => ({
  default: {
    create: () => ({
      get: axiosGet,
      defaults: {},
      interceptors: {
        request: { use: () => {} },
        response: { use: () => {} },
      },
    }),
  },
}));

import { api } from "../api.js";

describe("P0 claim #5 · dedupedGet cache key includes config.params", () => {
  beforeEach(() => {
    axiosGet.mockClear();
    axiosGet.mockResolvedValue({ data: { task: { status: "done" } } });
  });

  it("same url, DIFFERENT params → two underlying GETs (no collision)", async () => {
    const p1 = api.get("/cto/tasks/t-params-a", { params: { fields: "summary" } });
    const p2 = api.get("/cto/tasks/t-params-a", { params: { fields: "full" } });
    await Promise.all([p1, p2]);
    expect(axiosGet).toHaveBeenCalledTimes(2);
    expect(axiosGet).toHaveBeenCalledWith(
      "/cto/tasks/t-params-a", { params: { fields: "summary" } },
    );
    expect(axiosGet).toHaveBeenCalledWith(
      "/cto/tasks/t-params-a", { params: { fields: "full" } },
    );
  });

  it("same url, SAME params → one underlying GET (dedup still works)", async () => {
    await api.get("/cto/tasks/t-params-b", { params: { fields: "summary" } });
    await api.get("/cto/tasks/t-params-b", { params: { fields: "summary" } });
    expect(axiosGet).toHaveBeenCalledTimes(1);
  });

  it("param ORDER does not matter — {a,b} and {b,a} still coalesce", async () => {
    await api.get("/cto/tasks/t-params-c", { params: { a: "1", b: "2" } });
    await api.get("/cto/tasks/t-params-c", { params: { b: "2", a: "1" } });
    expect(axiosGet).toHaveBeenCalledTimes(1);
  });

  it("no params vs params → two underlying GETs", async () => {
    await api.get("/cto/tasks/t-params-d");
    await api.get("/cto/tasks/t-params-d", { params: { fields: "full" } });
    expect(axiosGet).toHaveBeenCalledTimes(2);
  });

  it("projects/list with different params → two underlying GETs", async () => {
    axiosGet.mockResolvedValue({ data: { projects: [] } });
    await api.get("/cto/projects/list", { params: { owner: "a" } });
    await api.get("/cto/projects/list", { params: { owner: "b" } });
    expect(axiosGet).toHaveBeenCalledTimes(2);
  });

  it("non-cached urls are untouched — params pass straight through", async () => {
    await api.get("/chat/history", { params: { session_id: "s1" } });
    await api.get("/chat/history", { params: { session_id: "s1" } });
    // No TTL for this path, so no coalescing at all.
    expect(axiosGet).toHaveBeenCalledTimes(2);
  });
});
