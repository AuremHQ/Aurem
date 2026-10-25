/**
 * billingModeCheck.test.js
 *
 * ITEM 5(b)/5(d) (independent review) — frontend/backend billing
 * flag drift detection. Compares the build-time VITE_TOKEN_BILLING_MODE
 * constant (frontend/src/lib/billingCopy.js) against the backend's
 * live runtime value (GET /payments/billing-mode). On mismatch: a
 * console warning + ONE analytics event, no UI change.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api", () => ({
  api: { get: vi.fn() },
}));

import { api } from "../api";
import { checkBillingModeDriftOnce, _resetBillingModeDriftCheckForTests } from "../billingModeCheck";

describe("checkBillingModeDriftOnce", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.gtag = vi.fn();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    _resetBillingModeDriftCheckForTests();
  });

  it("no warning/event when runtime matches build mode", async () => {
    api.get.mockResolvedValue({ data: { mode: "off" } });
    await checkBillingModeDriftOnce({ buildMode: "off" });
    expect(console.warn).not.toHaveBeenCalled();
    expect(window.gtag).not.toHaveBeenCalled();
  });

  it("warns and sends exactly one analytics event on mismatch", async () => {
    api.get.mockResolvedValue({ data: { mode: "on" } });
    await checkBillingModeDriftOnce({ buildMode: "off" });
    expect(console.warn).toHaveBeenCalledTimes(1);
    expect(console.warn.mock.calls[0][0]).toMatch(/billing.*mode/i);
    expect(window.gtag).toHaveBeenCalledTimes(1);
    expect(window.gtag).toHaveBeenCalledWith(
      "event", "billing_mode_drift",
      expect.objectContaining({ build_mode: "off", runtime_mode: "on" }),
    );
  });

  it("only runs once per page load even if called multiple times", async () => {
    api.get.mockResolvedValue({ data: { mode: "on" } });
    await checkBillingModeDriftOnce({ buildMode: "off" });
    await checkBillingModeDriftOnce({ buildMode: "off" });
    await checkBillingModeDriftOnce({ buildMode: "off" });
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(window.gtag).toHaveBeenCalledTimes(1);
  });

  it("never throws when the endpoint fails (network error, 429, etc.)", async () => {
    api.get.mockRejectedValue(new Error("network down"));
    await expect(checkBillingModeDriftOnce({ buildMode: "off" })).resolves.not.toThrow();
    expect(window.gtag).not.toHaveBeenCalled();
  });

  it("never throws when gtag is unavailable", async () => {
    delete window.gtag;
    api.get.mockResolvedValue({ data: { mode: "on" } });
    await expect(checkBillingModeDriftOnce({ buildMode: "off" })).resolves.not.toThrow();
  });

  it("an unrecognised/malformed runtime response is treated as no-data, never crashes, never falsely reports drift", async () => {
    api.get.mockResolvedValue({ data: {} });
    await checkBillingModeDriftOnce({ buildMode: "off" });
    expect(console.warn).not.toHaveBeenCalled();
    expect(window.gtag).not.toHaveBeenCalled();
  });
});

describe("fetchRuntimeBillingMode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns the mode string on success", async () => {
    const { fetchRuntimeBillingMode } = await import("../billingModeCheck");
    api.get.mockResolvedValue({ data: { mode: "shadow" } });
    const result = await fetchRuntimeBillingMode();
    expect(result).toBe("shadow");
  });

  it("returns null on failure, never throws", async () => {
    const { fetchRuntimeBillingMode } = await import("../billingModeCheck");
    api.get.mockRejectedValue(new Error("boom"));
    const result = await fetchRuntimeBillingMode();
    expect(result).toBeNull();
  });
});
