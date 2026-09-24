/**
 * ChatPanel.f12_retry_hijack_2026_02.test.jsx
 *
 * Founder QA finding (2026-02), SECOND independently-confirmed
 * instance of the same bug class first found earlier this
 * engagement ("Send to ORA" — a fresh user compose picking up a
 * stray F12 error payload). This time: clicking the "Retry" button
 * on a genuinely-timed-out task (data-testid="turn-retry-btn" →
 * `retryFailedTurn()`) returned a completely unrelated response
 * about a stray browser error ("js:317") instead of retrying the
 * original request ("update the footer text...").
 *
 * Root cause: `send()`'s F12-payload drain
 * (`window.__auremF12.flush()`) ran UNCONDITIONALLY on every call,
 * including internal/mechanical resends via `promptOverride`
 * (`retryFailedTurn`, `retryLastFix`, `continueTurn`, and the loop
 * "continue" call at ~L3503) — none of which are a fresh interactive
 * compose. Any stray console/network noise that happened to fire
 * during the wait (very plausible across a 48s+ timeout) got
 * silently attached to the RETRY of an unrelated prior request, and
 * the backend prioritized diagnosing that incidental payload over
 * the user's actual retry intent.
 *
 * Fix: gate the F12 drain on `promptOverride == null` — the SAME
 * signal `send()` already uses to skip every other fresh-compose-
 * only side effect (slash commands, ship-cancel detection,
 * session-start, clearing the input box — all a few lines away in
 * the same function).
 *
 * Source-level contract test, matching this codebase's own
 * established pattern for this class of bug (see
 * Session7_Item3_rapid_send_lock.test.jsx) — a full ChatPanel mount
 * with mocked SSE is disproportionate for what is fundamentally an
 * ordering/gating contract inside one function.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

const CHAT_PANEL_SRC = fs.readFileSync(
  path.resolve(__dirname, "../ChatPanel.jsx"),
  "utf-8",
);

describe("ChatPanel · F12 payload must not hijack promptOverride retries (2026-02)", () => {
  it("f12Payload computation is gated on promptOverride == null", () => {
    expect(CHAT_PANEL_SRC).toMatch(
      /const f12Payload = promptOverride != null\s*\n\s*\? null\s*\n\s*: \(lastF12PayloadRef\.current/,
    );
  });

  it("retryFailedTurn still resends the SAME original prompt via promptOverride (no regression to the retry itself)", () => {
    expect(CHAT_PANEL_SRC).toMatch(
      /function retryFailedTurn\(\)[\s\S]{0,300}send\(null, \{ promptOverride: lastUser\.content \}\)/,
    );
  });

  it("retryLastFix and continueTurn are also covered by the same promptOverride gate (not a one-off fix for only one caller)", () => {
    expect(CHAT_PANEL_SRC).toMatch(
      /function retryLastFix\(\)[\s\S]{0,300}send\(null, \{ promptOverride: lastUser\.content \}\)/,
    );
    expect(CHAT_PANEL_SRC).toMatch(
      /function continueTurn\(\)[\s\S]{0,300}send\(null, \{ promptOverride: ["']Please continue/,
    );
  });

  it("a real fresh compose (promptOverride == null) still drains and attaches window.__auremF12 — no regression to the legitimate 'Send to ORA' flow", () => {
    // The else-branch of the ternary must still do the real flush,
    // preferring the staged lastF12PayloadRef when present.
    expect(CHAT_PANEL_SRC).toMatch(
      /: \(lastF12PayloadRef\.current\s*\n\s*\|\|[\s\S]{0,150}window\.__auremF12[\s\S]{0,50}\.flush\(\)/,
    );
  });

  it("lastF12PayloadRef is still cleared after every send (including promptOverride sends) so a stale payload never leaks into a later, unrelated send", () => {
    expect(CHAT_PANEL_SRC).toMatch(
      /const f12Payload = promptOverride[\s\S]{0,400}lastF12PayloadRef\.current = null;/,
    );
  });
});
