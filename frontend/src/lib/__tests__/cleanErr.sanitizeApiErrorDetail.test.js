/**
 * cleanErr.sanitizeApiErrorDetail.test.js
 *
 * 2026-10-27 — CRITICAL regression. `services/github_errors.py`'s
 * structured `{code, message, retryable}` GitHub-failure detail,
 * combined with MessageBubble.jsx's OLD
 * `e?.response?.data?.detail || e?.message` catch logic, put a raw
 * OBJECT into `shipState.error` -> ShipApproveCard rendered `{error}`
 * directly as a React child -> "Objects are not valid as a React
 * child" -> the route error boundary replaced the ENTIRE chat page.
 * Live-reproduced by the testing agent. `sanitizeApiErrorDetail()`
 * (lib/cleanErr.js) is the shared fix used by both
 * MessageBubble.jsx's doSubmitShipTarget and SaveToGitHubModal.jsx —
 * this file locks its contract: the return value is ALWAYS a string,
 * never an object, and it NEVER falls back to a raw JS/axios
 * `e.message` (that's exactly the kind of infra leak — e.g. "Request
 * failed with status code 502" — this exists to prevent).
 */
import { describe, it, expect } from "vitest";
import { sanitizeApiErrorDetail } from "../cleanErr";

describe("sanitizeApiErrorDetail", () => {
  it("returns a plain STRING for a structured {code,message,retryable} object detail — never the object itself", () => {
    const e = {
      response: {
        status: 424,
        data: { detail: { code: "github_timeout", message: "Couldn't reach GitHub in time — please try again.", retryable: true } },
      },
    };
    const out = sanitizeApiErrorDetail(e, "fallback");
    expect(typeof out).toBe("string");
    expect(out).toBe("Couldn't reach GitHub in time — please try again.");
  });

  it("still returns a legacy plain-string detail unchanged", () => {
    const e = { response: { status: 403, data: { detail: "That repo isn't in your connected GitHub access." } } };
    expect(sanitizeApiErrorDetail(e, "fallback")).toBe("That repo isn't in your connected GitHub access.");
  });

  it("falls back to the caller's fallback string for a bare JS Error — NEVER e.message", () => {
    const e = new Error("Network Error");
    expect(sanitizeApiErrorDetail(e, "Submit failed")).toBe("Submit failed");
  });

  it("falls back to the caller's fallback string for an axios error with no detail (e.g. 'Request failed with status code 502') — NEVER e.message", () => {
    const e = { message: "Request failed with status code 502", response: { status: 502, data: {} } };
    expect(sanitizeApiErrorDetail(e, "Submit failed")).toBe("Submit failed");
  });

  it("sanitizes raw Cloudflare/HTML infra text that slips through as a string detail", () => {
    const e = { response: { status: 502, data: { detail: "<html><body>cloudflare 502 bad gateway</body></html>" } } };
    const out = sanitizeApiErrorDetail(e, "fallback");
    expect(out.toLowerCase()).not.toContain("<html");
    expect(out.toLowerCase()).not.toContain("cloudflare");
    expect(out).toContain("unreadable response");
  });

  it("an object detail missing .message falls back to the caller's fallback, not '[object Object]'", () => {
    const e = { response: { status: 424, data: { detail: { code: "github_error" } } } };
    expect(sanitizeApiErrorDetail(e, "Submit failed")).toBe("Submit failed");
  });

  it("a completely empty error object falls back cleanly", () => {
    expect(sanitizeApiErrorDetail({}, "Submit failed")).toBe("Submit failed");
    expect(sanitizeApiErrorDetail(null, "Submit failed")).toBe("Submit failed");
    expect(sanitizeApiErrorDetail(undefined, "Submit failed")).toBe("Submit failed");
  });
});
