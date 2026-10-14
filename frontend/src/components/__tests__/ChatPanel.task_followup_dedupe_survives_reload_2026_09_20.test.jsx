/**
 * ChatPanel.task_followup_dedupe_survives_reload_2026_09_20.test.jsx
 *
 * Founder report: old task-followup messages ("✅ Done — `sha`
 * pushed.", "❌ Task failed — nothing was committed") from Sept 13 /
 * Sept 16 re-appeared at the very bottom of the conversation, after
 * today's real last exchange, with nothing typed to trigger them.
 *
 * Root cause + full behavioural reproduction: see
 * utils/__tests__/mapHistoryTurn.test.js. This file locks in the
 * ChatPanel *wiring* itself (source-level, same approach as the other
 * ChatPanel.*.test.jsx files in this dir — ChatPanel is too heavy to
 * mount without full SSE/Mongo fixtures) so a future edit can't
 * quietly re-inline a stripped-down mapper or forget the import.
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

describe("ChatPanel — task-followup dedupe depends on mapHistoryTurn wiring", () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, "../ChatPanel.jsx"),
    "utf-8",
  ).replace(/\r\n/g, "\n");

  it("imports mapHistoryTurn from utils/dateDividers", () => {
    expect(src).toMatch(
      /import\s*\{[^}]*mapHistoryTurn[^}]*\}\s*from\s*"\.\.\/utils\/dateDividers"/,
    );
  });

  it("the history-load effect maps turns through mapHistoryTurn (not an inline object literal)", () => {
    expect(src).toContain(
      "setMessages(sortMessagesByTimestamp(turns.map(mapHistoryTurn)));",
    );
  });

  it("does NOT reintroduce an inline turns.map object literal that could omit kind/task_id", () => {
    // The old bug shape: a hand-rolled `turns.map((t) => ({ ... }))`
    // right before the sortMessagesByTimestamp call, which is exactly
    // how kind/task_id got silently dropped the first time.
    expect(src).not.toMatch(/turns\.map\(\(t\)\s*=>\s*\(\{/);
  });

  it("triggerTaskFollowup's dedupe check still keys on kind + task_id", () => {
    expect(src).toMatch(
      /msgs\.some\(\(mm\)\s*=>\s*mm\.kind === "task_followup"\s*\n?\s*&& mm\.task_id === taskId\)/,
    );
  });

  it("the live-appended followup message carries a real ts (defense-in-depth)", () => {
    const idx = src.indexOf('kind: "task_followup",\n            task_id: taskId,');
    expect(idx).toBeGreaterThan(-1);
    const nearby = src.slice(idx, idx + 600);
    expect(nearby).toContain("ts: Date.now() / 1000");
  });
});
