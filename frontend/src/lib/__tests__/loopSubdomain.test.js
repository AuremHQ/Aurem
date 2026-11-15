/**
 * loopSubdomain.test.js — 2026-11-15, Nov 10 subdomain decision.
 * The hostname check is the gate for the whole subdomain move: a
 * wrong "true" would swap the ORA homepage for the harness page, a
 * wrong "false" would leave loop.auremcto.com showing the ORA site.
 */
import { describe, it, expect } from "vitest";
import { isLoopSubdomain, LOOP_SUBDOMAIN_HOST } from "../loopSubdomain";

describe("isLoopSubdomain", () => {
  it("is true only on the exact subdomain host", () => {
    expect(LOOP_SUBDOMAIN_HOST).toBe("loop.auremcto.com");
    expect(isLoopSubdomain("loop.auremcto.com")).toBe(true);
  });

  it("is false on the main domain and www", () => {
    expect(isLoopSubdomain("auremcto.com")).toBe(false);
    expect(isLoopSubdomain("www.auremcto.com")).toBe(false);
  });

  it("is false on lookalike / suffix hosts", () => {
    expect(isLoopSubdomain("loop.auremcto.com.evil.com")).toBe(false);
    expect(isLoopSubdomain("notloop.auremcto.com")).toBe(false);
    expect(isLoopSubdomain("loop.auremcto.comx")).toBe(false);
  });

  it("is case-insensitive and ignores surrounding whitespace", () => {
    expect(isLoopSubdomain("LOOP.AUREMCTO.COM")).toBe(true);
    expect(isLoopSubdomain("  loop.auremcto.com  ")).toBe(true);
  });

  it("is false for empty / missing host and for localhost", () => {
    expect(isLoopSubdomain("")).toBe(false);
    expect(isLoopSubdomain(undefined)).toBe(false);
    expect(isLoopSubdomain("localhost")).toBe(false);
    expect(isLoopSubdomain("127.0.0.1")).toBe(false);
  });

  it("reads window.location.hostname when no argument is given", () => {
    // jsdom default host is localhost — never the loop subdomain.
    expect(isLoopSubdomain()).toBe(false);
  });
});
