/**
 * loopApi.part13.test.js — Part XIII (ORA audit fix lane).
 *
 * Locks in:
 *   1. startLoop threads the chat review mode (swift / pro / maxx)
 *      through to POST /loop/start (was omitted entirely).
 *   2. rollbackLoop forwards acknowledge_drift (and force) so a
 *      drift-blocked rollback can be retried with acknowledgement.
 *      The default call still sends exactly { confirm: "ROLLBACK" }.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api", () => ({
  api: { post: vi.fn(), get: vi.fn() },
  API_BASE: "/api/aurem-dev",
}));

import { api } from "../api";
import { startLoop, rollbackLoop } from "../loopApi";

beforeEach(() => {
  vi.clearAllMocks();
  api.post.mockResolvedValue({ data: { ok: true } });
});

describe("Part XIII · startLoop sends mode (2a)", () => {
  it("sends mode: maxx when the caller passes it", async () => {
    await startLoop({
      projectId: "p1", userMessage: "ship it", sessionId: "s1", mode: "maxx",
    });
    expect(api.post).toHaveBeenCalledWith("/loop/start", {
      project_id: "p1",
      user_message: "ship it",
      session_id: "s1",
      mode: "maxx",
    });
  });

  it("sends mode: pro when the caller passes it", async () => {
    await startLoop({
      projectId: "p1", userMessage: "ship it", sessionId: "s1", mode: "pro",
    });
    expect(api.post).toHaveBeenCalledWith(
      "/loop/start",
      expect.objectContaining({ mode: "pro" }),
    );
  });

  it("sends mode: null when the caller passes none (backend = full Council)", async () => {
    await startLoop({ projectId: "p1", userMessage: "ship it", sessionId: "s1" });
    expect(api.post).toHaveBeenCalledWith("/loop/start", expect.objectContaining({
      mode: null,
    }));
  });

  it("returns the response data", async () => {
    api.post.mockResolvedValueOnce({ data: { loop_id: "loop_1", mode: "maxx" } });
    const r = await startLoop({
      projectId: "p1", userMessage: "hi", sessionId: "s1", mode: "maxx",
    });
    expect(r).toEqual({ loop_id: "loop_1", mode: "maxx" });
  });
});

describe("Part XIII · rollbackLoop forwards acknowledge_drift (1)", () => {
  it("default call sends exactly { confirm: 'ROLLBACK' } (no regression)", async () => {
    await rollbackLoop("loop_1");
    expect(api.post).toHaveBeenCalledWith(
      "/loop/loop_1/rollback",
      { confirm: "ROLLBACK" },
    );
  });

  it("sends acknowledge_drift: true when the caller passes it", async () => {
    await rollbackLoop("loop_1", { acknowledgeDrift: true });
    expect(api.post).toHaveBeenCalledWith(
      "/loop/loop_1/rollback",
      { confirm: "ROLLBACK", acknowledge_drift: true },
    );
  });

  it("sends force: true when the caller passes it (failed-retry path)", async () => {
    await rollbackLoop("loop_1", { force: true });
    expect(api.post).toHaveBeenCalledWith(
      "/loop/loop_1/rollback",
      { confirm: "ROLLBACK", force: true },
    );
  });

  it("does NOT send acknowledge_drift when false (backend default applies)", async () => {
    await rollbackLoop("loop_1", { acknowledgeDrift: false });
    expect(api.post).toHaveBeenCalledWith(
      "/loop/loop_1/rollback",
      { confirm: "ROLLBACK" },
    );
  });

  it("returns the response data (drift responses included)", async () => {
    api.post.mockResolvedValueOnce({
      data: {
        ok: false, loop_id: "loop_1", rollback_status: "drift_detected",
        drift: { branch: "main", expected: "aaa", current: "bbb" },
      },
    });
    const r = await rollbackLoop("loop_1");
    expect(r.rollback_status).toBe("drift_detected");
    expect(r.ok).toBe(false);
  });
});
