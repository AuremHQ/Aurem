/**
 * ChatPanel.oraAuditP0.test.jsx — ORA audit P0 (claim #7 + extra #1).
 *
 * Source-contract test, following the repo's own convention for
 * ChatPanel ("too heavy to mount in jsdom (SSE + Mongo fixtures)",
 * see ChatPanel.attach.test.jsx): assert on the component source.
 *
 * Claim #7: the stream callbacks in send()/runTurn() mutated
 * copy[copy.length - 1] positionally, so a late frame from an
 * aborted/retried stream landed on whatever assistant bubble was
 * last. Fix: every callback updates the turn's bubble by its
 * per-turn id via updateTurnBubble(), and onTaskHandoff — the only
 * callback that lacked one — gained the `streaming` guard.
 *
 * Extra #1: the history effect never cleared messages at effect
 * start and its .catch deliberately preserved the old messages on
 * any fetch failure, so a failed history fetch after a project
 * switch left the previous session's conversation visible under
 * the new project's header. Fix: clear synchronously on a real
 * session switch (and when the session id goes null), and clear to
 * [WELCOME] on a fetch error when nothing was painted from this
 * session's own cache.
 */
import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";

const SRC = readFileSync("src/components/ChatPanel.jsx", "utf-8");

function sliceBetween(src, startMarker, endMarker) {
  const start = src.indexOf(startMarker);
  const end = src.indexOf(endMarker, start + startMarker.length);
  if (start < 0 || end < 0) throw new Error(`markers not found: ${startMarker} → ${endMarker}`);
  return src.slice(start, end);
}

describe("P0 claim #7 · stream callbacks update by bubble id, never positionally", () => {
  it("the turn's assistant bubble carries a per-turn id", () => {
    expect(SRC).toContain("const turnBubbleId =");
    expect(SRC).toContain("crypto.randomUUID()");
    expect(SRC).toContain("id: turnBubbleId");
  });

  it("updateTurnBubble() matches by id and leaves state untouched on no-match / failed guard", () => {
    expect(SRC).toContain("const updateTurnBubble = (msgs, guard, patch) => {");
    expect(SRC).toContain("msgs.findIndex((m) => m && m.id === turnBubbleId)");
    expect(SRC).toContain("if (idx < 0) return msgs;");
    expect(SRC).toContain("if (!guard(target)) return msgs;");
  });

  it("no positional copy[copy.length - 1] mutation remains in the send/runTurn flow", () => {
    // The only remaining occurrence is the explanatory comment on
    // the helper itself; every code site was converted.
    const hits = SRC.match(/copy\[copy\.length - 1\]/g) || [];
    expect(hits.length).toBe(1);
    const line = SRC.split("\n").find((l) => l.includes("copy[copy.length - 1]"));
    expect(line.trim().startsWith("//")).toBe(true);
  });

  it.each([
    ["onMode", "onMode: (m) => {", "onCouncil:"],
    ["onCouncil", "onCouncil: (n) => {", "onTaskHandoff:"],
    ["onTaskHandoff", "onTaskHandoff: (p) => {", "onMeta:"],
    ["onMeta", "onMeta: (m) => {", "onStep:"],
    ["onStep", "onStep: (s) => {", "onIntent:"],
    ["onThinking", "onThinking: (elapsed,", "onToken:"],
    ["onToken", "onToken: (tok) => {", "onWatchdogPending:"],
    ["onWatchdogPending", "onWatchdogPending: () => {", "onWatchdog:"],
    ["onWatchdog", "onWatchdog: (wd) => {", "onScopeConfirmation:"],
    ["onDone", "onDone: (d) => {", "onError: (err) => {"],
  ])("%s updates via updateTurnBubble (id-matched)", (_name, start, end) => {
    const block = sliceBetween(SRC, start, end);
    expect(block).toContain("updateTurnBubble(msgs,");
    expect(block).not.toContain("copy[copy.length - 1]");
  });

  it("onTaskHandoff gained the missing `streaming` guard", () => {
    const block = sliceBetween(SRC, "onTaskHandoff: (p) => {", "onMeta:");
    expect(block).toContain("(last) => last.role === \"assistant\" && last.streaming,");
  });

  it("onError's preceding-user-bubble mark hangs off the matched bubble's index, not length - 2", () => {
    const block = sliceBetween(SRC, "onError: (err) => {", "setBusy(false);");
    expect(block).toContain("msgs.findIndex((m) => m && m.id === turnBubbleId)");
    expect(block).toContain("const userIdx = idx - 1;");
    expect(block).not.toContain("copy.length - 2");
  });

  it("performRetry's reset + fail paths are id-matched too", () => {
    const block = sliceBetween(SRC, "function performRetry(idleFor,", "await streamChat({");
    expect(block).toContain("updateTurnBubble(msgs,");
    expect(block).not.toContain("copy[copy.length - 1]");
  });
});

describe("P0 extra #1 · history effect never shows the previous session's turns after a switch", () => {
  it("blanks messages when the session id goes null (Shell's project-switch clear)", () => {
    const block = sliceBetween(SRC, "// Load history on session change", "api\n      .get(`/chat/history`");
    expect(block).toContain("if (!sessionId) {");
    expect(block).toContain("setMessages([]);");
    expect(block).toContain("prevHistorySessionIdRef");
  });

  it("clears synchronously on a real session switch when no cache was painted", () => {
    const block = sliceBetween(SRC, "// Load history on session change", "api\n      .get(`/chat/history`");
    expect(block).toContain("historySessionSwitched");
    expect(block).toContain("if (historySessionSwitched) {");
  });

  it("on history-fetch error with no cache paint, clears to [WELCOME] instead of preserving", () => {
    const block = sliceBetween(SRC, ".catch((err) => {", ".finally(() => !cancelled");
    expect(block).toContain("if (!paintedFromCache) {");
    expect(block).toContain("setMessages([WELCOME]);");
  });

  it("the error-path clear is gated on !paintedFromCache (Iter 330 invariant)", () => {
    // The clear sits INSIDE the !paintedFromCache guard, and it is
    // the only setMessages in the error path: a fetch failure after
    // a cache paint keeps THIS session's own turns (the case Iter
    // 330's preserve rule was written for).
    const block = sliceBetween(SRC, ".catch((err) => {", ".finally(() => !cancelled");
    const guardIdx = block.indexOf("if (!paintedFromCache) {");
    const clearIdx = block.indexOf("setMessages([WELCOME]);");
    expect(guardIdx).toBeGreaterThanOrEqual(0);
    expect(clearIdx).toBeGreaterThan(guardIdx);
    expect(block.match(/setMessages\(/g).length).toBe(1);
  });
});
