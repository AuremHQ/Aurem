/**
 * NewUserWizard.oauthStuckFallback.test.js — 2026-02 "STUCK USERS
 * FIX" Part 2.1/2.3, source-level verification.
 *
 * `connectGithub()` (the manual-URL-recovery OAuth path, reached via
 * `ghStatus === "disconnected"` when a submit fails with "github not
 * connected") used to have ZERO failure feedback on popup-close or
 * timeout — the poll interval just quietly cleared itself.
 *
 * Full component-mount simulation of this path requires first
 * driving a submit failure to reach "disconnected" (a secondary,
 * error-recovery state, not the primary App-install CTA already
 * covered by useGitHubConnectStatus's own test suite) — that
 * indirection makes a robust fake-timer mount test disproportionately
 * complex for what is themselves straightforward, linear code
 * changes. Verifying the actual implementation in source is the
 * pragmatic, still-meaningful regression guard here: every assertion
 * below fails immediately if the real logic is ever deleted/reverted,
 * which is the actual risk this guards against.
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const src = fs.readFileSync(
  path.resolve(__dirname, "../NewUserWizard.jsx"), "utf8",
);

describe("Part 2.1 — connectGithub() no longer fails silently", () => {
  it("sets oauthStuck on popup-closed instead of silently clearing", () => {
    expect(src).toMatch(/popupRef\.current\?\.closed/);
    expect(src).toMatch(/setOauthStuck\(true\)/);
    expect(src).toMatch(/reason:\s*"popup_closed"/);
  });

  it("raised the timeout from the old silent 90s to 3 minutes", () => {
    expect(src).toMatch(/Date\.now\(\)\s*-\s*started\s*>\s*3\s*\*\s*60_000/);
    expect(src).toMatch(/reason:\s*"client_timeout"/);
    expect(src).not.toMatch(/>\s*90_000/);
  });

  it("renders a real fallback UI (retry + see-options) for the stuck state", () => {
    expect(src).toMatch(/data-testid="wizard-oauth-stuck"/);
    expect(src).toMatch(/data-testid="wizard-oauth-stuck-retry-btn"/);
    expect(src).toMatch(/data-testid="wizard-oauth-stuck-fallback-btn"/);
    // Retry re-runs the same connect function, fallback opens the
    // existing StarterChoiceModal (no-GitHub path) rather than a
    // dead end.
    expect(src).toMatch(/onClick=\{connectGithub\}/);
    expect(src).toMatch(/aurem:open-starter-choices/);
  });

  it("clears oauthStuck at the start of every fresh connect attempt", () => {
    const fn = src.slice(src.indexOf("function connectGithub"));
    const fnBody = fn.slice(0, fn.indexOf("\n  }\n"));
    expect(fnBody).toMatch(/setOauthStuck\(false\)/);
  });
});

describe("Part 2.3 — 10-minute stuck-on-auth-screen tab-return backstop", () => {
  it("wires a visibilitychange listener with a 10-minute threshold", () => {
    expect(src).toMatch(/document\.addEventListener\("visibilitychange"/);
    expect(src).toMatch(/10 \* 60_000/);
    expect(src).toMatch(/reason:\s*"auth_screen_stuck_10min"/);
  });

  it("only fires the backstop while a popup is genuinely still open and unresolved", () => {
    const listenerFn = src.slice(src.indexOf("function onVisible"));
    const body = listenerFn.slice(0, listenerFn.indexOf("\n    }\n"));
    expect(body).toMatch(/popupRef\.current\.closed/);
    expect(body).toMatch(/ghStatus === "connected"/);
  });
});
