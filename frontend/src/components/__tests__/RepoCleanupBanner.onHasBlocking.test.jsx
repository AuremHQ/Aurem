/**
 * RepoCleanupBanner.onHasBlocking.test.jsx — UI-fix D3 (2026-10)
 *
 * Dashboard.jsx's chat-pane banner stack caps itself at 2 items —
 * the blocking repo-cleanup banner (priority 1) + at most one
 * next-action nudge (priority 2) — by reading this new
 * `onHasBlocking` callback instead of stacking a 2nd/3rd card
 * whenever this banner already has something to show.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../Toast", () => ({ toast: vi.fn() }));

import RepoCleanupBanner from "../RepoCleanupBanner";
import { api } from "../../lib/api";

beforeEach(() => vi.clearAllMocks());

describe("UI-fix D3 · RepoCleanupBanner reports its blocking state", () => {
  it("calls onHasBlocking(true) and renders the banner when broken repos exist", async () => {
    api.get.mockResolvedValue({ data: { broken: [{ project_id: "p1", reason: "repo_not_found" }] } });
    const onHasBlocking = vi.fn();
    render(<RepoCleanupBanner onHasBlocking={onHasBlocking} />);
    await screen.findByTestId("repo-cleanup-banner");
    await waitFor(() => expect(onHasBlocking).toHaveBeenLastCalledWith(true));
  });

  it("calls onHasBlocking(false) and renders nothing when there's nothing broken", async () => {
    api.get.mockResolvedValue({ data: { broken: [] } });
    const onHasBlocking = vi.fn();
    render(<RepoCleanupBanner onHasBlocking={onHasBlocking} />);
    await waitFor(() => expect(onHasBlocking).toHaveBeenLastCalledWith(false));
    expect(screen.queryByTestId("repo-cleanup-banner")).toBeNull();
  });

  it("works with no onHasBlocking prop at all (backward compatible)", async () => {
    api.get.mockResolvedValue({ data: { broken: [] } });
    expect(() => render(<RepoCleanupBanner />)).not.toThrow();
  });
});
