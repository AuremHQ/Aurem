/**
 * Shell.oraAuditP0.test.jsx — ORA audit P0 (claim #1 + extra #3).
 *
 * Claim #1: Shell's session-resolution effect resolved a new
 * sessionId (or deferred minting 3000 ms) without clearing the old
 * id first, so a message sent right after a project switch paired
 * the NEW project with the OLD session (the send path only gates on
 * the id being truthy). Fix: clear the id synchronously on a real
 * project change (same batch as the change) AND first thing in the
 * resolution effect, so the 3 s defer window shows null, not the
 * stale id.
 *
 * Extra #3: the ?session= deep link was re-read on every project
 * switch. openSession() leaves it in the URL on purpose (shareable
 * link), so a later switch re-adopted a stale id from the previous
 * project. Fix: only the first run of the effect (a real page load)
 * may honour it; later runs strip it instead.
 *
 * Runtime tests mount the real Shell with its heavy children mocked
 * out (the session logic itself is fully real). Static locks pin the
 * ordering the runtime tests can't see (clear-before-resolve).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, screen, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const SHELL_SRC = readFileSync(resolve(__dirname, "..", "Shell.jsx"), "utf-8");

const { apiGet, healthGet, newSessionId } = vi.hoisted(() => {
  let n = 0;
  return {
    apiGet: vi.fn(),
    healthGet: vi.fn(),
    newSessionId: () => `s-mock-${++n}`,
  };
});

vi.mock("../../lib/api", () => ({
  api: { get: apiGet, delete: vi.fn() },
  healthApi: { get: healthGet },
  getUser: () => JSON.parse(localStorage.getItem("aurem_user") || "null"),
  getToken: () => localStorage.getItem("aurem_token"),
  logout: vi.fn(),
  newSessionId,
  setUser: vi.fn(),
}));
vi.mock("../../lib/cacheCleaner", () => ({ clearUICacheAndReload: vi.fn() }));
vi.mock("../Toast", () => ({ toast: vi.fn() }));
vi.mock("../TokenMeter", () => ({ default: () => null }));
vi.mock("../FloatingORAButton", () => ({ default: () => null }));
vi.mock("../ClearCacheButton", () => ({ default: () => null }));
vi.mock("../ThemeToggle", () => ({ default: () => null }));
vi.mock("../DeleteChatConfirmModal", () => ({ default: () => null }));

import Shell, { useChatSession } from "../Shell.jsx";

// jsdom has no matchMedia; Shell reads it in a useState initializer.
if (typeof window.matchMedia !== "function") {
  window.matchMedia = (q) => ({
    matches: false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  });
}

function Probe() {
  const { sessionId } = useChatSession();
  return <div data-testid="probe-session">{sessionId || "(null)"}</div>;
}

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Shell requireAuth={false}>
        <Probe />
      </Shell>
    </MemoryRouter>,
  );
}

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe("P0 claim #1 · static source lock — clear lands before any resolution", () => {
  it("the resolution effect clears the session id before any adopt/mint path", () => {
    const effectStart = SHELL_SRC.indexOf("useEffect(() => {\n    // P0 audit fix (claim #1)");
    expect(effectStart).toBeGreaterThanOrEqual(0);
    const clearIdx = SHELL_SRC.indexOf("setSessionIdState(null);", effectStart);
    const mintIdx = SHELL_SRC.indexOf("mintFresh()", effectStart);
    const adoptIdx = SHELL_SRC.indexOf('setSessionIdState(decision.id)', effectStart);
    expect(clearIdx).toBeGreaterThanOrEqual(0);
    expect(clearIdx).toBeLessThan(mintIdx);
    expect(clearIdx).toBeLessThan(adoptIdx);
  });

  it("the project-changed listener clears only on a REAL change (redundant re-dispatches don't wedge the chat)", () => {
    expect(SHELL_SRC).toContain("if (next !== activeProjectIdRef.current) setSessionIdState(null);");
  });

  it("the ?session= deep link is honoured only on the first effect run", () => {
    expect(SHELL_SRC).toContain("firstSessionEffectRunRef");
    expect(SHELL_SRC).toContain("if (urlSessionId && !isFirstSessionEffectRun) {");
  });
});

describe("P0 claim #1 · runtime — session clears on project switch, stays null through the defer window", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState({}, "", "/dashboard");
    localStorage.setItem("aurem_token", "t");
    localStorage.setItem("aurem_user", JSON.stringify({ name: "Teji" }));
    apiGet.mockImplementation((url) => {
      if (url === "/team/me") return Promise.resolve({ data: { permissions: {} } });
      if (url === "/chat/sessions") return Promise.resolve({ data: { sessions: [] } });
      return Promise.resolve({ data: {} });
    });
    healthGet.mockResolvedValue({ data: { ok: true } });
  });
  afterEach(() => { vi.useRealTimers(); });

  it("project switch clears the old id at once; the defer window shows null, never the stale id", async () => {
    // Project A with a live session.
    localStorage.setItem("aurem_active_project", "p_a");
    localStorage.setItem("aurem_session_proj_p_a", "s_old");
    sessionStorage.setItem("aurem_session_proj_p_a", "s_old");

    renderShell();
    await flush();
    expect(screen.getByTestId("probe-session").textContent).toBe("s_old");

    // Switch away: active project becomes null (auto-seed pending)
    // while the projects cache says a project exists → the effect
    // DEFERS minting for 3 s. The old id must be gone immediately
    // and stay gone until the fallback mints.
    await act(async () => {
      localStorage.removeItem("aurem_active_project");
      localStorage.setItem("aurem_projects_cache", JSON.stringify([{ project_id: "p_b" }]));
      window.dispatchEvent(new Event("aurem:project-changed"));
    });
    expect(screen.getByTestId("probe-session").textContent).toBe("(null)");

    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(screen.getByTestId("probe-session").textContent).toBe("(null)");

    await act(async () => { vi.advanceTimersByTime(2500); });
    const afterFallback = screen.getByTestId("probe-session").textContent;
    expect(afterFallback).not.toBe("(null)");
    expect(afterFallback).not.toBe("s_old");
    expect(afterFallback).toMatch(/^s-mock-/);
  });

  it("a direct project switch (A → B) never exposes the old session id to the new project", async () => {
    localStorage.setItem("aurem_active_project", "p_a");
    localStorage.setItem("aurem_session_proj_p_a", "s_old");
    sessionStorage.setItem("aurem_session_proj_p_a", "s_old");

    renderShell();
    await flush();
    expect(screen.getByTestId("probe-session").textContent).toBe("s_old");

    await act(async () => {
      localStorage.setItem("aurem_active_project", "p_b");
      window.dispatchEvent(new Event("aurem:project-changed"));
    });
    const after = screen.getByTestId("probe-session").textContent;
    expect(after).not.toBe("s_old");
    expect(after).toMatch(/^s-mock-/);
  });

  it("a redundant re-dispatch of the SAME project id does not clear the session", async () => {
    localStorage.setItem("aurem_active_project", "p_a");
    localStorage.setItem("aurem_session_proj_p_a", "s_old");
    sessionStorage.setItem("aurem_session_proj_p_a", "s_old");

    renderShell();
    await flush();
    expect(screen.getByTestId("probe-session").textContent).toBe("s_old");

    await act(async () => {
      window.dispatchEvent(new Event("aurem:project-changed"));
    });
    expect(screen.getByTestId("probe-session").textContent).toBe("s_old");
  });
});

describe("P0 extra #3 · runtime — ?session= is a fresh-load link only", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem("aurem_token", "t");
    localStorage.setItem("aurem_user", JSON.stringify({ name: "Teji" }));
    apiGet.mockImplementation((url) => {
      if (url === "/team/me") return Promise.resolve({ data: { permissions: {} } });
      if (url === "/chat/sessions") return Promise.resolve({ data: { sessions: [] } });
      return Promise.resolve({ data: {} });
    });
    healthGet.mockResolvedValue({ data: { ok: true } });
  });
  afterEach(() => { vi.useRealTimers(); });

  it("a later project switch never re-adopts a stale ?session= left by openSession", async () => {
    // Open a deep link for project A (first run honours it).
    localStorage.setItem("aurem_active_project", "p_a");
    window.history.replaceState({}, "", "/dashboard?session=s_deep_a");

    renderShell();
    await flush();
    expect(screen.getByTestId("probe-session").textContent).toBe("s_deep_a");
    // Consumed on first run: stripped from the URL.
    expect(window.location.search).toBe("");

    // Simulate openSession() leaving a fresh ?session= in the URL
    // (it navigates without re-running the effect), then switch.
    window.history.replaceState({}, "", "/dashboard?session=s_deep_a");
    await act(async () => {
      localStorage.setItem("aurem_active_project", "p_b");
      window.dispatchEvent(new Event("aurem:project-changed"));
    });

    const after = screen.getByTestId("probe-session").textContent;
    expect(after).not.toBe("s_deep_a");
    expect(after).toMatch(/^s-mock-/);
    // And the stale param is stripped, not left for the next switch.
    expect(window.location.search).toBe("");
    expect(localStorage.getItem("aurem_session_proj_p_b")).not.toBe("s_deep_a");
  });
});
