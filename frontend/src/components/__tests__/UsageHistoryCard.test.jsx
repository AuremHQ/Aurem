/**
 * UsageHistoryCard.test.jsx — Master Prompt v2 PART 4.3/4.4.
 * "Usage history lists rows" (date/action/tokens) + never exposes
 * cost/model/provider, scoped to the caller via the real backend
 * endpoint (mocked here at the api layer).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import UsageHistoryCard from "../UsageHistoryCard.jsx";
import { api } from "../../lib/api";

beforeEach(() => { vi.clearAllMocks(); });

describe("UsageHistoryCard", () => {
  it("lists rows with date/action/tokens from the ledger endpoint", async () => {
    api.get.mockResolvedValue({
      data: { rows: [
        { date: "2026-10-20", action: "chat", tokens: 15 },
        { date: "2026-10-19", action: "fix", tokens: 20 },
      ] },
    });
    render(<UsageHistoryCard />);
    await waitFor(() => expect(screen.getAllByTestId("usage-history-row")).toHaveLength(2));
    expect(api.get).toHaveBeenCalledWith("/usage/me/ledger?days=30");
    expect(screen.getByText("chat")).toBeInTheDocument();
    expect(screen.getByText("fix")).toBeInTheDocument();
  });

  it("shows an empty state, not an error, when there's no history yet", async () => {
    api.get.mockResolvedValue({ data: { rows: [] } });
    render(<UsageHistoryCard />);
    await waitFor(() => expect(screen.getByTestId("usage-history-empty")).toBeInTheDocument());
  });

  it("degrades to the empty state on a fetch failure rather than crashing", async () => {
    api.get.mockRejectedValue(new Error("network"));
    render(<UsageHistoryCard />);
    await waitFor(() => expect(screen.getByTestId("usage-history-empty")).toBeInTheDocument());
  });

  it("never renders cost, model, or provider even if the API response accidentally included them", async () => {
    api.get.mockResolvedValue({
      data: { rows: [{ date: "2026-10-20", action: "chat", tokens: 15, cost_usd: 77.0, model: "secret-model", provider: "secret-provider" }] },
    });
    const { container } = render(<UsageHistoryCard />);
    await waitFor(() => expect(screen.getByTestId("usage-history-row")).toBeInTheDocument());
    const text = container.textContent;
    expect(text).not.toContain("77");
    expect(text).not.toContain("secret-model");
    expect(text).not.toContain("secret-provider");
  });
});
