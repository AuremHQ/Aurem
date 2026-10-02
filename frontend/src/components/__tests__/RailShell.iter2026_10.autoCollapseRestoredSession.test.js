/**
 * 2026-10 — "Sidebar AUTO does not collapse" root-cause fix.
 *
 * Reported bug: with AUTO on, the rail never collapses for a
 * returning user (i.e. any project/chat that already has prior
 * messages — the overwhelming majority of real sessions).
 *
 * Root cause: `ChatPanel.jsx` had ONE ref (`sessionStartedRef`)
 * guarding BOTH:
 *   1. the history-restore dispatch (`restored: true`, fires on
 *      every mount where history has turns) — which RailShell.jsx
 *      intentionally ignores (`if (e?.detail?.restored) return;`);
 *   2. the real-send dispatch (no `restored` flag) — the ONLY one
 *      RailShell actually acts on to collapse the rail.
 *
 * Because the restore-path dispatch fires FIRST and flips the
 * shared ref to `true`, the real-send dispatch's own
 * `if (!sessionStartedRef.current ...)` guard was already false by
 * the time the user sent a message — so it never fired, and the
 * rail never collapsed, for any session with prior history.
 *
 * Fix: split into two independent refs. `sessionStartedRef` keeps
 * guarding the restore-path dispatch only; a new `realSendStartedRef`
 * exclusively guards the real-send dispatch RailShell listens to.
 *
 * These tests lock the fix via source inspection (matching this
 * file's existing sibling test style) so a future refactor that
 * re-merges the two refs fails fast.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const CHAT_PANEL = path.resolve(
  __dirname, "..", "..", "components", "ChatPanel.jsx",
);

describe("ChatPanel — restored history must not block the real-send AUTO dispatch", () => {
  const src = fs.readFileSync(CHAT_PANEL, "utf-8");

  it("declares a dedicated ref for the real-send dispatch, separate from the restore-path ref", () => {
    expect(src).toMatch(/const sessionStartedRef\s*=\s*useRef\(false\)/);
    expect(src).toMatch(/const realSendStartedRef\s*=\s*useRef\(false\)/);
  });

  it("the history-restore dispatch (restored: true) only sets sessionStartedRef, never realSendStartedRef", () => {
    const restoreBlock = src.match(
      /if \(!sessionStartedRef\.current\) \{[\s\S]*?detail: \{ session_id: sessionId, restored: true \}[\s\S]*?\}\s*\n\s*\}/,
    );
    expect(restoreBlock, "restore-path dispatch block not found").toBeTruthy();
    expect(restoreBlock[0]).toMatch(/sessionStartedRef\.current = true/);
    expect(restoreBlock[0]).not.toMatch(/realSendStartedRef/);
  });

  it("the real-send dispatch (no restored flag) is gated by realSendStartedRef, not sessionStartedRef", () => {
    const sendGateIdx = src.indexOf("if (!realSendStartedRef.current && promptOverride == null)");
    expect(sendGateIdx).toBeGreaterThan(-1);
    const nearby = src.slice(sendGateIdx, sendGateIdx + 400);
    expect(nearby).toMatch(/detail: \{ session_id: sessionId \}/);
    expect(nearby).not.toMatch(/restored:\s*true/);
  });

  it("both refs reset together on session change (new chat / switched project)", () => {
    const resetBlock = src.match(/useEffect\(\(\) => \{\s*sessionStartedRef\.current = false;\s*realSendStartedRef\.current = false;/);
    expect(resetBlock, "session-change reset effect not found or refs reset out of sync").toBeTruthy();
  });
});
