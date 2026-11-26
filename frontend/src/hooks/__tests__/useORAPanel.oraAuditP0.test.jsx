/**
 * useORAPanel.oraAuditP0.test.jsx — ORA audit P0 (claims #3 + #7).
 *
 * Claim #3 (second half): openPanel() mints a fresh ora-panel-…
 * session id on every open but never reset the messages array, so a
 * reopen showed the previous conversation under a brand-new session.
 * Fix: openPanel purges messages back to the fresh welcome.
 *
 * Claim #7 (useORAPanel half): the stream token/done/error handlers
 * and stopStream() mutated copy[copy.length - 1] positionally (the
 * error handler with no guard at all). Fix: every update matches the
 * streaming bubble by its per-turn id, and a late frame after a
 * purge / stop can no longer touch another turn's message.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useORAPanel } from "../useORAPanel.js";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn() },
  API_BASE: "/api/aurem-dev",
  getUser: () => ({ name: "Teji Tester" }),
}));
vi.mock("../../components/TabBar", () => ({
  getActiveProjectId: () => "p1",
  setActiveProjectId: vi.fn(),
}));

import { api } from "../../lib/api";

// Controllable SSE reader: the test decides exactly when each frame
// arrives, so a purge/stop can be interleaved mid-stream.
function makeFetchHarness() {
  const queued = [];
  const waiters = [];
  const reader = {
    read: vi.fn(() => {
      if (queued.length) return Promise.resolve(queued.shift());
      return new Promise((resolve) => waiters.push(resolve));
    }),
    cancel: vi.fn(),
  };
  const deliver = (result) => {
    if (waiters.length) waiters.shift()(result);
    else queued.push(result);
  };
  const pushFrame = (obj) => {
    const bytes = new TextEncoder().encode(`data: ${JSON.stringify(obj)}\n`);
    deliver({ done: false, value: bytes });
  };
  const pushEnd = () => deliver({ done: true, value: undefined });
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    body: { getReader: () => reader },
  });
  return { pushFrame, pushEnd };
}

describe("P0 claim #3 · openPanel purges the previous conversation", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("aurem_token", "jwt-test");
    api.get.mockResolvedValue({
      data: { projects: [{ project_id: "p1", github_owner: "o", github_repo: "r" }] },
    });
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it("reopening the panel resets messages to the fresh welcome", async () => {
    const { pushFrame, pushEnd } = makeFetchHarness();
    const { result } = renderHook(() => useORAPanel());

    await act(async () => { result.current.openPanel(); });
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toContain("Welcome to AUREM, Teji");

    // One full turn: user + streamed assistant reply.
    let sendPromise;
    act(() => { sendPromise = result.current.sendMessage("hello"); });
    await act(async () => {});
    pushFrame({ token: "Hi " });
    await act(async () => {});
    pushFrame({ token: "there" });
    await act(async () => {});
    pushFrame({ done: true });
    pushEnd();
    await act(async () => { await sendPromise; });

    expect(result.current.messages).toHaveLength(3);
    expect(result.current.messages[2].content).toBe("Hi there");
    expect(result.current.messages[2].streaming).toBe(false);

    // Reopen: the old conversation belongs to a dead session.
    await act(async () => { result.current.openPanel(); });
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toContain("Welcome to AUREM, Teji");
    expect(result.current.messages[0].role).toBe("assistant");
  });

  it("a late token arriving AFTER a reopen never resurrects the purged turn", async () => {
    const { pushFrame, pushEnd } = makeFetchHarness();
    const { result } = renderHook(() => useORAPanel());

    await act(async () => { result.current.openPanel(); });
    let sendPromise;
    act(() => { sendPromise = result.current.sendMessage("hello"); });
    await act(async () => {});
    pushFrame({ token: "partial" });
    await act(async () => {});
    expect(result.current.messages[2].content).toBe("partial");
    expect(result.current.messages[2].streaming).toBe(true);

    // Reopen mid-stream: purge the in-flight turn.
    await act(async () => { result.current.openPanel(); });
    expect(result.current.messages).toHaveLength(1);

    // The old stream's late frames land after the purge.
    pushFrame({ token: " LATE" });
    await act(async () => {});
    pushFrame({ done: true });
    pushEnd();
    await act(async () => { await sendPromise; });

    // Still just the fresh welcome — no resurrected partial turn,
    // no error bubble stamped onto the new conversation.
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toContain("Welcome to AUREM, Teji");
  });

  it("stopStream stops only the live bubble; a late token after stop is dropped", async () => {
    const { pushFrame, pushEnd } = makeFetchHarness();
    const { result } = renderHook(() => useORAPanel());

    await act(async () => { result.current.openPanel(); });
    let sendPromise;
    act(() => { sendPromise = result.current.sendMessage("hello"); });
    await act(async () => {});
    pushFrame({ token: "partial" });
    await act(async () => {});
    expect(result.current.messages[2].content).toBe("partial");

    act(() => { result.current.stopStream(); });
    expect(result.current.messages[2].streaming).toBe(false);
    expect(result.current.messages[2].stopped).toBe(true);
    expect(result.current.messages[2].content).toBe("partial");

    // Late token from the aborted stream must not append / resurrect.
    pushFrame({ token: " LATE" });
    await act(async () => {});
    expect(result.current.messages[2].content).toBe("partial");
    expect(result.current.messages[2].streaming).toBe(false);

    pushEnd();
    await act(async () => { await sendPromise; });
    expect(result.current.messages[2].content).toBe("partial");
  });

  it("a stream error lands on the live bubble, not on whatever message is last", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500, body: null });
    const { result } = renderHook(() => useORAPanel());

    await act(async () => { result.current.openPanel(); });
    await act(async () => { await result.current.sendMessage("hello"); });

    expect(result.current.messages).toHaveLength(3);
    expect(result.current.messages[2].error).toBe(true);
    expect(result.current.messages[2].content).toContain("HTTP 500");
    expect(result.current.messages[2].streaming).toBe(false);
  });
});
