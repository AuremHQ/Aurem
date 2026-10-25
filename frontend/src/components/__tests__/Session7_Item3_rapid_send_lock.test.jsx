/**
 * Session 7 · Item 3 regression contract — rapid double-send lock.
 *
 * Real-user QA: sending two rapid chat/loop messages back-to-back
 * (~1s gap) triggered the app's own F12 error-capture widget to
 * show "1 console error" → "2 console errors". Root cause: React's
 * `busy` state is async — rapid double-clicks let both send()
 * invocations read `busy=false` from stale closures and both pass
 * the guard, spawning two racing startLoop/chat API calls (second
 * one hits 409 conflict → surfaces on console).
 *
 * Fix: synchronous `sendInFlightRef` ref-lock armed BEFORE any
 * await point in `send()`, released either when `busy` toggles back
 * to false (useEffect on [busy]) or via a 5 s safety timeout.
 *
 * Assertions locked at source level — testing the real double-send
 * behavior end-to-end requires a full ChatPanel mount with mocked
 * axios + SSE, which is disproportionate for what is fundamentally
 * a single-ref-guard contract.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

const CHAT_PANEL_SRC = fs.readFileSync(
  path.resolve(__dirname, "../ChatPanel.jsx"),
  "utf-8",
);

describe("Session 7 · Item 3 · Rapid-send race guard", () => {
  it("sendInFlightRef is declared", () => {
    expect(CHAT_PANEL_SRC).toContain(
      "const sendInFlightRef = useRef(false)");
  });

  it("send() checks the ref BEFORE any await point", () => {
    // CI-REPAIR — this used to be a fixed-character-distance regex
    // (`{0,2000}`) which went stale as more documentation comments were
    // added to send()'s body over time (verified: the real distance from
    // "async function send(" to the sendInFlightRef check is now 3130
    // chars, not a behaviour change, just accumulated comments). Replaced
    // with the SAME position/indexOf convention the next test in this
    // file ("lock is armed AFTER validity guards") already uses — real
    // structural ordering, not a brittle char-budget.
    const sendFnIdx = CHAT_PANEL_SRC.indexOf("async function send(");
    expect(sendFnIdx).toBeGreaterThan(-1);
    const refCheckIdx = CHAT_PANEL_SRC.indexOf("sendInFlightRef.current", sendFnIdx);
    expect(refCheckIdx).toBeGreaterThan(sendFnIdx);
    const dupLogIdx = CHAT_PANEL_SRC.indexOf("dropped rapid duplicate", refCheckIdx);
    expect(dupLogIdx).toBeGreaterThan(refCheckIdx);
  });

  it("lock is armed AFTER validity guards, not before", () => {
    // Otherwise an invalid send (empty text) would arm the lock
    // and starve the next legitimate send. Checked by POSITION
    // within the send() function body (guard must appear before the
    // lock-arm line) — Master Prompt v2 PART 5.3 red-team sweep fix:
    // this used to slice a fixed `sendFnIdx + 8000`-char window
    // before searching, the SAME brittle-char-budget pattern the
    // sibling test above this one already replaced once (see its
    // own comment). An unrelated one-line import/JSX edit elsewhere
    // in ChatPanel.jsx pushed the real distance to 8382 chars,
    // exceeding the window and making BOTH indexOf calls below
    // return -1 — not a behavior change, just the same stale
    // char-budget bug recurring in the one place it wasn't fixed
    // yet. Now scoped the same way the sibling test does: search the
    // FULL source, just bounded to "found after sendFnIdx" — no
    // magic window size to go stale again. `!sessionId` also appears
    // elsewhere in the file (outside send()), so this is scoped to
    // the send() function body first via that same anchor.
    const sendFnIdx = CHAT_PANEL_SRC.indexOf("async function send(");
    expect(sendFnIdx).toBeGreaterThan(-1);
    const guardIdx = CHAT_PANEL_SRC.indexOf("!sessionId", sendFnIdx);
    const lockIdx = CHAT_PANEL_SRC.indexOf("sendInFlightRef.current = true", sendFnIdx);
    expect(guardIdx).toBeGreaterThan(sendFnIdx);
    expect(lockIdx).toBeGreaterThan(sendFnIdx);
    expect(guardIdx).toBeLessThan(lockIdx);
  });

  it("lock releases on busy transition to false via useEffect", () => {
    expect(CHAT_PANEL_SRC).toMatch(
      /useEffect\(\(\) => \{[\s\S]{0,500}!busy[\s\S]{0,400}sendInFlightRef\.current = false/);
  });

  it("belt-and-braces 5 s safety timeout is armed", () => {
    // If a promise crashes without setBusy(false), the safety timer
    // still releases the lock so the next send isn't stuck.
    expect(CHAT_PANEL_SRC).toMatch(
      /sendLockTimeoutRef\.current = setTimeout\([\s\S]{0,300}sendInFlightRef\.current = false[\s\S]{0,80}5000/);
  });

  it("debug log fires on dropped duplicate (aids diagnosis)", () => {
    // Should be a console.debug (NOT error) so we can trace race
    // condition hits in prod without surfacing them as user-visible
    // errors on the F12 widget.
    expect(CHAT_PANEL_SRC).toMatch(
      /console\.debug\(["'`]\[send\] dropped rapid duplicate/);
  });
});
