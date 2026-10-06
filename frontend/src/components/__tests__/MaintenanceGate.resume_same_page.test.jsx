/**
 * After a deploy the user must land back exactly where they were, fast:
 * the app stays mounted under the "Brief Hiccup" screen (typed text and
 * page survive) and the screen clears within ~a poll (500ms) of the
 * backend returning — not up to 3s later.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.hoisted(() => { process.env.REACT_APP_BACKEND_URL = "http://vitest.local"; });

import MaintenanceGate from "../MaintenanceGate.jsx";
import { shouldReloadForChunkError, installChunkReload } from "../../lib/chunkReload.js";

let backendUp;
beforeEach(() => {
  vi.useFakeTimers();
  backendUp = true;
  localStorage.clear();
  global.fetch = vi.fn(async () => {
    if (!backendUp) throw new Error("connection refused");
    return { ok: true, json: async () => ({ manual_enabled: false }) };
  });
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const tick = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/projects/abc"]}>
      <MaintenanceGate>
        <input data-testid="draft" defaultValue="" />
      </MaintenanceGate>
    </MemoryRouter>,
  );
}

describe("MaintenanceGate — resume same page", () => {
  it("keeps the app mounted (typed text survives) and clears fast on recovery", async () => {
    renderApp();
    await tick(10);
    fireEvent.change(screen.getByTestId("draft"), { target: { value: "half-typed message" } });

    backendUp = false;                       // deploy starts
    await tick(3100); await tick(3100);      // 2 failed polls → screen
    expect(screen.getByTestId("maintenance-screen")).toBeTruthy();
    // the page underneath was NOT unmounted
    expect(screen.getByTestId("draft").value).toBe("half-typed message");

    backendUp = true;                        // deploy finished
    await tick(600);                         // < 1 blocked-poll interval + jitter
    expect(screen.queryByTestId("maintenance-screen")).toBeNull();
    expect(screen.getByTestId("draft").value).toBe("half-typed message");
  });

  it("re-checks immediately when the browser comes back online", async () => {
    renderApp();
    await tick(10);
    backendUp = false;
    await tick(3100); await tick(3100);
    expect(screen.getByTestId("maintenance-screen")).toBeTruthy();
    backendUp = true;
    await act(async () => { window.dispatchEvent(new Event("online")); await vi.advanceTimersByTimeAsync(5); });
    expect(screen.queryByTestId("maintenance-screen")).toBeNull();
  });
});

describe("chunk reload after deploy", () => {
  beforeEach(() => sessionStorage.clear());

  it("reloads once, then not again within 30s (no loop)", () => {
    expect(shouldReloadForChunkError(1_000_000)).toBe(true);
    expect(shouldReloadForChunkError(1_005_000)).toBe(false);
    expect(shouldReloadForChunkError(1_031_000)).toBe(true);
  });

  it("vite:preloadError triggers a reload", () => {
    const reload = vi.fn();
    const off = installChunkReload(window, reload);
    window.dispatchEvent(new Event("vite:preloadError"));
    expect(reload).toHaveBeenCalledTimes(1);
    off();
  });
});
