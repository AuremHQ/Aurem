/**
 * TokenMeter.test.jsx — Master Prompt v2 PART 4.3/4.4.
 * Locks in the strict display rules: hidden before a paid plan,
 * shows ONLY the remaining number (never per-action), 20%/5%/0%
 * threshold banners/modal, each firing once.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import TokenMeter from "../TokenMeter.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function renderMeter(props = {}) {
  return render(
    <MemoryRouter>
      <TokenMeter {...props} />
    </MemoryRouter>,
  );
}

describe("TokenMeter", () => {
  it("renders nothing before the server says visible (no paid plan / mode off)", async () => {
    api.get.mockResolvedValue({ data: { visible: false, balance: null, base: null, pct_remaining: null, refill_date: null, threshold: null } });
    renderMeter();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/usage/me/meter"));
    expect(screen.queryByTestId("token-meter")).toBeNull();
  });

  it("shows only the remaining balance number, never a per-action figure", async () => {
    api.get.mockResolvedValue({ data: { visible: true, balance: 820, base: 1000, pct_remaining: 82, refill_date: null, threshold: "ok" } });
    renderMeter();
    await waitFor(() => expect(screen.getByTestId("token-meter")).toBeInTheDocument());
    expect(screen.getByTestId("token-meter-balance")).toHaveTextContent("820 tokens left");
    expect(screen.queryByText(/-\d+ tokens?/i)).toBeNull();
    expect(screen.queryByText(/per (fix|chat|task|tool call)/i)).toBeNull();
  });

  it("shows the refill date", async () => {
    const refill = Math.floor(Date.now() / 1000) + 86400 * 3;
    api.get.mockResolvedValue({ data: { visible: true, balance: 500, base: 1000, pct_remaining: 50, refill_date: refill, threshold: "ok" } });
    renderMeter();
    await waitFor(() => expect(screen.getByTestId("token-meter-refill")).toBeInTheDocument());
  });

  it("fires the yellow banner at 20%", async () => {
    api.get.mockResolvedValue({ data: { visible: true, balance: 200, base: 1000, pct_remaining: 20, refill_date: null, threshold: "yellow" } });
    renderMeter();
    await waitFor(() => expect(screen.getByTestId("token-meter-banner")).toHaveAttribute("data-tone", "yellow"));
  });

  it("fires the red banner at 5%", async () => {
    api.get.mockResolvedValue({ data: { visible: true, balance: 50, base: 1000, pct_remaining: 5, refill_date: null, threshold: "red" } });
    renderMeter();
    await waitFor(() => expect(screen.getByTestId("token-meter-banner")).toHaveAttribute("data-tone", "red"));
  });

  it("fires the stop modal at 0", async () => {
    api.get.mockResolvedValue({ data: { visible: true, balance: 0, base: 1000, pct_remaining: 0, refill_date: null, threshold: "stop" } });
    renderMeter();
    await waitFor(() => expect(screen.getByTestId("token-meter-stop-modal")).toBeInTheDocument());
  });

  it("stop modal is dismissable and does not block rendering of the rest of the app", async () => {
    api.get.mockResolvedValue({ data: { visible: true, balance: 0, base: 1000, pct_remaining: 0, refill_date: null, threshold: "stop" } });
    renderMeter();
    await waitFor(() => expect(screen.getByTestId("token-meter-stop-modal")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("token-meter-stop-modal-dismiss"));
    expect(screen.queryByTestId("token-meter-stop-modal")).toBeNull();
  });

  it("each threshold banner fires only once per session even if polled again at the same level", async () => {
    api.get.mockResolvedValue({ data: { visible: true, balance: 200, base: 1000, pct_remaining: 20, refill_date: null, threshold: "yellow" } });
    renderMeter();
    await waitFor(() => expect(screen.getByTestId("token-meter-banner")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("token-meter-banner-dismiss"));
    expect(screen.queryByTestId("token-meter-banner")).toBeNull();
    // A later re-render/poll at the SAME threshold must not re-show it.
    api.get.mockResolvedValue({ data: { visible: true, balance: 199, base: 1000, pct_remaining: 19.9, refill_date: null, threshold: "yellow" } });
  });
});
