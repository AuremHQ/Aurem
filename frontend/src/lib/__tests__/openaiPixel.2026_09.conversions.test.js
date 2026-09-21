/**
 * 2026-09 — OpenAI (ChatGPT) Ads Measurement Pixel conversion helpers.
 *
 * Locks in the contract for `openaiRegistrationCompleted`,
 * `openaiGithubRepoConnected`, and `openaiSubscriptionCreated` in
 * `lib/analytics.js`:
 *
 *   • Each helper must call `window.oaiq('measure', <eventName>,
 *     params [, { event_id }])` with the correct custom event name.
 *   • The GitHub event MUST fire as "github_repo_connected" — NOT the
 *     generic "github_connected" name.
 *   • When `window.oaiq` is undefined (ad-blocker / SSR / pre-init)
 *     every helper must silently return `false` — never throw.
 *   • `openaiSubscriptionCreated` MUST guardrail against invalid
 *     inputs (null / NaN / <= 0 value, missing / non-string currency)
 *     and report `amount` in minor currency units (cents).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  openaiRegistrationCompleted,
  openaiGithubRepoConnected,
  openaiSubscriptionCreated,
} from "../analytics";

describe("2026-09 — OpenAI Ads Pixel conversion helpers", () => {
  beforeEach(() => {
    delete window.oaiq;
  });

  describe("no-op when oaiq is unavailable", () => {
    it("openaiRegistrationCompleted returns false without throwing", () => {
      expect(() => openaiRegistrationCompleted("user_1")).not.toThrow();
      expect(openaiRegistrationCompleted("user_1")).toBe(false);
    });

    it("openaiGithubRepoConnected returns false without throwing", () => {
      expect(() => openaiGithubRepoConnected("proj_1")).not.toThrow();
      expect(openaiGithubRepoConnected("proj_1")).toBe(false);
    });

    it("openaiSubscriptionCreated returns false without throwing", () => {
      expect(() => openaiSubscriptionCreated(9, "USD", "cs_test_1")).not.toThrow();
      expect(openaiSubscriptionCreated(9, "USD", "cs_test_1")).toBe(false);
    });
  });

  describe("openaiRegistrationCompleted", () => {
    it("fires oaiq('measure', 'registration_completed', {}, { event_id })", () => {
      const oaiq = vi.fn();
      window.oaiq = oaiq;

      const ok = openaiRegistrationCompleted("user_42");

      expect(ok).toBe(true);
      expect(oaiq).toHaveBeenCalledTimes(1);
      expect(oaiq).toHaveBeenCalledWith(
        "measure",
        "registration_completed",
        {},
        { event_id: "user_42" }
      );
    });

    it("omits event_id when no user id is available", () => {
      const oaiq = vi.fn();
      window.oaiq = oaiq;

      openaiRegistrationCompleted();

      expect(oaiq).toHaveBeenCalledWith("measure", "registration_completed", {}, undefined);
    });
  });

  describe("openaiGithubRepoConnected", () => {
    it("fires the custom 'github_repo_connected' event (NOT 'github_connected')", () => {
      const oaiq = vi.fn();
      window.oaiq = oaiq;

      const ok = openaiGithubRepoConnected("proj_99");

      expect(ok).toBe(true);
      expect(oaiq).toHaveBeenCalledWith(
        "measure",
        "github_repo_connected",
        {},
        { event_id: "proj_99" }
      );
      expect(oaiq.mock.calls[0][1]).not.toBe("github_connected");
    });
  });

  describe("openaiSubscriptionCreated", () => {
    it("fires 'subscription_created' with amount in cents + currency + sid dedup", () => {
      const oaiq = vi.fn();
      window.oaiq = oaiq;

      const ok = openaiSubscriptionCreated(19, "USD", "cs_test_pro_1");

      expect(ok).toBe(true);
      expect(oaiq).toHaveBeenCalledWith(
        "measure",
        "subscription_created",
        { type: "contents", amount: 1900, currency: "USD" },
        { event_id: "cs_test_pro_1" }
      );
    });

    it("still fires without a sid but omits event_id", () => {
      const oaiq = vi.fn();
      window.oaiq = oaiq;

      const ok = openaiSubscriptionCreated(9, "USD");

      expect(ok).toBe(true);
      expect(oaiq).toHaveBeenCalledWith(
        "measure",
        "subscription_created",
        { type: "contents", amount: 900, currency: "USD" },
        undefined
      );
    });

    it("GUARDRAIL: skips when value is null / NaN / <= 0", () => {
      const oaiq = vi.fn();
      window.oaiq = oaiq;

      expect(openaiSubscriptionCreated(null, "USD", "cs_x")).toBe(false);
      expect(openaiSubscriptionCreated(undefined, "USD", "cs_x")).toBe(false);
      expect(openaiSubscriptionCreated(0, "USD", "cs_x")).toBe(false);
      expect(openaiSubscriptionCreated(-5, "USD", "cs_x")).toBe(false);
      expect(openaiSubscriptionCreated("not-a-number", "USD", "cs_x")).toBe(false);

      expect(oaiq).not.toHaveBeenCalled();
    });

    it("GUARDRAIL: skips when currency is missing or non-string", () => {
      const oaiq = vi.fn();
      window.oaiq = oaiq;

      expect(openaiSubscriptionCreated(9, null, "cs_x")).toBe(false);
      expect(openaiSubscriptionCreated(9, undefined, "cs_x")).toBe(false);
      expect(openaiSubscriptionCreated(9, "", "cs_x")).toBe(false);
      expect(openaiSubscriptionCreated(9, 123, "cs_x")).toBe(false);

      expect(oaiq).not.toHaveBeenCalled();
    });

    it("swallows exceptions from oaiq (never throws to business flow)", () => {
      window.oaiq = () => {
        throw new Error("adblock injected");
      };

      expect(() => openaiSubscriptionCreated(9, "USD", "cs_x")).not.toThrow();
      expect(openaiSubscriptionCreated(9, "USD", "cs_x")).toBe(false);
    });
  });
});
