/**
 * useTokenMeter.test.js
 *
 * ITEM 8 (independent review) — extracted from TokenMeter.jsx's own
 * inline fetch/poll logic so Settings.jsx's balance card can read
 * from the EXACT SAME data source (GET /usage/me/meter) instead of
 * the legacy, non-gating /usage/me `remaining` number it used
 * before. Both consumers now call this one hook.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import { api } from "../../lib/api";
import { useTokenMeter, METER_CACHE_KEY } from "../useTokenMeter";

function Harness() {
  const { meter, loading } = useTokenMeter();
  if (loading) return <div data-testid="state">loading</div>;
  if (!meter || !meter.visible) return <div data-testid="state">hidden</div>;
  return <div data-testid="state">{meter.balance}</div>;
}

describe("useTokenMeter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem(METER_CACHE_KEY);
  });

  it("fetches /usage/me/meter on mount", async () => {
    api.get.mockResolvedValue({ data: { visible: false, balance: null } });
    render(<Harness />);
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/usage/me/meter"));
  });

  it("exposes visible:false as hidden state", async () => {
    api.get.mockResolvedValue({ data: { visible: false, balance: null, base: null, pct_remaining: null, refill_date: null, threshold: null } });
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("hidden"));
  });

  it("exposes the real balance when visible", async () => {
    api.get.mockResolvedValue({ data: { visible: true, balance: 820, base: 1000, pct_remaining: 82, refill_date: null, threshold: "ok" } });
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("820"));
  });

  it("never throws when the endpoint fails — degrades to hidden/loading, not a crash", async () => {
    api.get.mockRejectedValue(new Error("network down"));
    expect(() => render(<Harness />)).not.toThrow();
  });

  it("re-polls on an interval (same 60s cadence as the original TokenMeter inline logic)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.get.mockResolvedValue({ data: { visible: true, balance: 100, base: 1000, pct_remaining: 10, refill_date: null, threshold: "yellow" } });
    render(<Harness />);
    await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    vi.useRealTimers();
  });
});

describe("ITEM 8 — TokenMeter and a Settings-shaped consumer show the SAME number for the same mocked API response", () => {
  beforeEach(() => vi.clearAllMocks());

  it("both render 820 from one shared /usage/me/meter mock", async () => {
    const { MemoryRouter } = await import("react-router-dom");
    const { default: TokenMeter } = await import("../../components/TokenMeter.jsx");
    api.get.mockResolvedValue({ data: { visible: true, balance: 820, base: 1000, pct_remaining: 82, refill_date: null, threshold: "ok" } });

    render(
      <MemoryRouter>
        <TokenMeter />
        <Harness />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByTestId("token-meter-balance")).toHaveTextContent("820 tokens left"));
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("820"));
  });

  // Minor 13 - TOKENS USED must not show a dash at first paint: a cached
  // meter from the previous visit paints immediately, before the fetch.
  it("paints the cached meter immediately, before the fetch resolves", () => {
    localStorage.setItem(
      METER_CACHE_KEY,
      JSON.stringify({ visible: true, balance: 820, base: 1000, pct_remaining: 82, refill_date: null, threshold: "ok" }),
    );
    api.get.mockImplementation(() => new Promise(() => {}));
    render(<Harness />);
    expect(screen.getByTestId("state")).toHaveTextContent("820");
  });

  it("writes the fetched meter to the cache", async () => {
    api.get.mockResolvedValue({ data: { visible: true, balance: 820, base: 1000, pct_remaining: 82, refill_date: null, threshold: "ok" } });
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("820"));
    expect(JSON.parse(localStorage.getItem(METER_CACHE_KEY))).toMatchObject({ visible: true, balance: 820 });
  });
});
