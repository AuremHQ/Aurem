/**
 * PaywallModal.test.jsx — Master Prompt v2 PART 4.2/4.4.
 * "Fix opens plans when locked; no fix request before the server
 * says entitled; modal closable and fix stays locked" + the
 * client-side-unlock-attempt security case (forged ?upgraded=1
 * never unlocks without a real server-confirmed poll).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

import PaywallModal from "../PaywallModal.jsx";
import { api } from "../../lib/api";

const originalLocation = window.location;

function setUrl(search) {
  delete window.location;
  window.location = { ...originalLocation, search, origin: "https://app.test", href: "" };
}

beforeEach(() => {
  vi.clearAllMocks();
  setUrl("");
});

afterEach(() => {
  window.location = originalLocation;
});

describe("PaywallModal", () => {
  it("renders nothing when closed", () => {
    render(<PaywallModal open={false} onClose={() => {}} />);
    expect(screen.queryByTestId("paywall-modal")).toBeNull();
  });

  it("shows the plans + ONE estimate line when opened", async () => {
    api.get.mockResolvedValue({ data: { price_usd: 9, estimated_fixes: 23.4 } });
    render(<PaywallModal open={true} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("paywall-modal-estimate")).toBeInTheDocument());
    expect(screen.getByTestId("paywall-modal-estimate").textContent).toContain("23 fixes");
  });

  it("never shows a refund line (none configured anywhere in this repo)", async () => {
    api.get.mockResolvedValue({ data: { price_usd: 9, estimated_fixes: 10 } });
    render(<PaywallModal open={true} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("paywall-modal")).toBeInTheDocument());
    expect(screen.queryByTestId("paywall-modal-refund")).toBeNull();
    expect(screen.getByTestId("paywall-modal").textContent.toLowerCase()).not.toContain("refund");
  });

  it("is closable and never calls onUnlocked just from closing", async () => {
    api.get.mockResolvedValue({ data: { price_usd: 9, estimated_fixes: 10 } });
    const onClose = vi.fn();
    const onUnlocked = vi.fn();
    render(<PaywallModal open={true} onClose={onClose} onUnlocked={onUnlocked} />);
    await waitFor(() => expect(screen.getByTestId("paywall-modal-close")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("paywall-modal-close"));
    expect(onClose).toHaveBeenCalled();
    expect(onUnlocked).not.toHaveBeenCalled();
  });

  it("clicking the overlay also closes without unlocking", async () => {
    api.get.mockResolvedValue({ data: { price_usd: 9, estimated_fixes: 10 } });
    const onClose = vi.fn();
    const onUnlocked = vi.fn();
    render(<PaywallModal open={true} onClose={onClose} onUnlocked={onUnlocked} />);
    await waitFor(() => expect(screen.getByTestId("paywall-modal-overlay")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("paywall-modal-overlay"));
    expect(onClose).toHaveBeenCalled();
    expect(onUnlocked).not.toHaveBeenCalled();
  });

  it("a forged ?upgraded=1 with NO session_id never triggers a poll or unlock", async () => {
    setUrl("?upgraded=1");
    api.get.mockResolvedValue({ data: { price_usd: 9, estimated_fixes: 10 } });
    const onUnlocked = vi.fn();
    render(<PaywallModal open={true} onClose={() => {}} onUnlocked={onUnlocked} />);
    await waitFor(() => expect(screen.getByTestId("paywall-modal")).toBeInTheDocument());
    // Only the fix-estimate call should have happened — never a
    // /payments/status poll, since there's no session_id to poll.
    const statusCalls = api.get.mock.calls.filter((c) => String(c[0]).includes("/payments/status/"));
    expect(statusCalls).toHaveLength(0);
    expect(onUnlocked).not.toHaveBeenCalled();
  });

  it("unlocks ONLY when the server poll reports fix_entitled true — not from the URL", async () => {
    setUrl("?session_id=cs_test_123&upgraded=1");
    api.get.mockImplementation((url) => {
      if (String(url).includes("/payments/status/")) {
        return Promise.resolve({ data: { fix_entitled: true } });
      }
      return Promise.resolve({ data: { price_usd: 9, estimated_fixes: 10 } });
    });
    const onUnlocked = vi.fn();
    render(<PaywallModal open={true} onClose={() => {}} onUnlocked={onUnlocked} />);
    await waitFor(() => expect(onUnlocked).toHaveBeenCalled());
    expect(api.get).toHaveBeenCalledWith("/payments/status/cs_test_123");
  });

  it("a pending (not-yet-paid) session_id keeps polling and does NOT unlock", async () => {
    setUrl("?session_id=cs_test_pending");
    api.get.mockImplementation((url) => {
      if (String(url).includes("/payments/status/")) {
        return Promise.resolve({ data: { fix_entitled: false, payment_status: "unpaid" } });
      }
      return Promise.resolve({ data: { price_usd: 9, estimated_fixes: 10 } });
    });
    const onUnlocked = vi.fn();
    render(<PaywallModal open={true} onClose={() => {}} onUnlocked={onUnlocked} />);
    await waitFor(() => expect(screen.getByTestId("paywall-modal-polling")).toBeInTheDocument());
    expect(onUnlocked).not.toHaveBeenCalled();
  });

  it("a 404'd (forged/stale/cross-user) session_id never unlocks", async () => {
    setUrl("?session_id=cs_test_forged");
    api.get.mockImplementation((url) => {
      if (String(url).includes("/payments/status/")) {
        return Promise.reject({ response: { status: 404 } });
      }
      return Promise.resolve({ data: { price_usd: 9, estimated_fixes: 10 } });
    });
    const onUnlocked = vi.fn();
    render(<PaywallModal open={true} onClose={() => {}} onUnlocked={onUnlocked} />);
    await waitFor(() => expect(screen.getByTestId("paywall-modal-polling")).toBeInTheDocument());
    expect(onUnlocked).not.toHaveBeenCalled();
  });
});
