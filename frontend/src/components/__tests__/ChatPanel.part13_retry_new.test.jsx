/**
 * ChatPanel.part13_retry_new.test.jsx — Part XIII · 2(a) + 2(c).
 *
 * Source-contract test (established pattern: ChatPanel is too heavy
 * to mount in jsdom — see ChatPanel.attach.test.jsx). Locks in:
 *   • runLoopPlan threads chatMode into startLoop (mode: chatMode).
 *   • The failed loop's original brief is saved at runLoopPlan time
 *     and carried on the loopFailure payload.
 *   • handleRetryAsNewLoop starts a FRESH loop via runLoopPlan with
 *     that brief — never pauseResponse (terminal loops 409).
 *   • LoopFailureCard is wired with originalMessage +
 *     onRetryAsNewLoop={handleRetryAsNewLoop}.
 *   • LiveTaskPopup retry swaps the popup to the new task (4a).
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const SRC = fs.readFileSync(
  path.resolve(__dirname, "../ChatPanel.jsx"),
  "utf-8",
);

describe("Part XIII · 2(a) — ChatPanel threads chatMode into startLoop", () => {
  it("runLoopPlan passes mode: chatMode to startLoop", () => {
    // The call spans a comment block between sessionId and mode.
    expect(SRC).toMatch(/await startLoop\(\{\s*projectId:[\s\S]{0,900}mode:\s*chatMode,/);
  });

  it("does not hardcode a mode in the startLoop call", () => {
    const call = SRC.match(/await startLoop\(\{[\s\S]*?\}\);/);
    expect(call).not.toBeNull();
    expect(call[0]).not.toMatch(/mode:\s*"(swift|pro|maxx)"/);
  });
});

describe("Part XIII · 2(c) — ChatPanel 'Retry as new loop' wiring", () => {
  it("saves the composed brief at runLoopPlan time", () => {
    expect(SRC).toContain("lastLoopBriefRef.current = composed");
  });

  it("carries the original brief on the loopFailure payload", () => {
    expect(SRC).toContain("originalMessage: lastLoopBriefRef.current");
  });

  it("defines handleRetryAsNewLoop that re-runs runLoopPlan with the brief", () => {
    expect(SRC).toContain("async function handleRetryAsNewLoop");
    expect(SRC).toMatch(
      /handleRetryAsNewLoop[\s\S]{0,600}await runLoopPlan\(brief, \[\], \{\}\)/,
    );
  });

  it("handleRetryAsNewLoop clears the failure card before re-running", () => {
    const fn = SRC.match(/async function handleRetryAsNewLoop[\s\S]*?\n  \}/);
    expect(fn).not.toBeNull();
    expect(fn[0]).toContain("setLoopFailure(null)");
  });

  it("handleRetryAsNewLoop never calls pauseResponse (no resurrection)", () => {
    const fn = SRC.match(/async function handleRetryAsNewLoop[\s\S]*?\n  \}/);
    expect(fn).not.toBeNull();
    expect(fn[0]).not.toContain("pauseResponse");
    expect(fn[0]).not.toContain("confirmLoop");
  });

  it("wires the card with originalMessage + onRetryAsNewLoop", () => {
    expect(SRC).toContain("originalMessage={loopFailure.originalMessage}");
    expect(SRC).toContain("onRetryAsNewLoop={handleRetryAsNewLoop}");
  });

  it("does not change the 409 terminal behaviour (no retry of FAILED loops)", () => {
    // runLoopPlan is the only loop-start path the handler uses; the
    // pause-response 409 guard lives in the backend and is untouched.
    expect(SRC).toContain("handleRetryAsNewLoop");
    expect(SRC).not.toMatch(/handleRetryAsNewLoop[\s\S]{0,400}pauseResponse/);
  });
});

describe("Part XIII · 4(a) — ChatPanel wires LiveTaskPopup retry", () => {
  it("passes onRetry that swaps the popup to the retried task", () => {
    expect(SRC).toContain("onRetry={(tid) => setLivePopupTaskId(tid)}");
  });
});
