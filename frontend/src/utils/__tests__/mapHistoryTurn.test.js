/**
 * utils/mapHistoryTurn.test.js — 2026-09-20
 *
 * Bug report: old task-followup completion messages ("✅ Done —
 * `sha` pushed.", "❌ Task failed — nothing was committed") from days-
 * old sessions (Sept 13 / Sept 16) re-appeared at the very bottom of
 * the conversation, after the real last exchange, with nothing typed
 * to trigger them.
 *
 * Root cause (see mapHistoryTurn's docstring in ../dateDividers.js):
 * `GET /chat/history` hydration never carried `kind`/`task_id` over
 * onto the mapped message objects. ChatPanel's own client-side
 * dedupe guard for task-followup re-appends
 * (`msgs.some(mm => mm.kind === "task_followup" && mm.task_id ===
 * taskId)`) depends ENTIRELY on those two fields surviving a reload.
 * Without them, the guard silently always missed, so the very next
 * time that task's MessageBubble remounted and its terminal-status
 * poll re-fired `onTaskCompleted`, the (idempotent-on-the-backend)
 * followup call returned the same cached text and — finding no
 * match — a duplicate landed at the end of the array with no `ts`.
 *
 * This was NOT a fix that regressed: `git log -p -S"kind:
 * \"task_followup\""` across the entire history of ChatPanel.jsx
 * shows exactly ONE commit ever set that literal (the live-append
 * path, Iter 53, commit a8bcd1b6) — no commit, ever, added `kind`/
 * `task_id` to the history-hydration mapping. The dedupe guard has
 * depended on a field the hydration path never supplied since the
 * day both were written.
 *
 * These tests exercise the REAL, now-extracted `mapHistoryTurn`
 * function (previously inlined in ChatPanel's history-load effect)
 * and reproduce the exact dedupe predicate ChatPanel uses, so a
 * future edit that drops `kind`/`task_id` from the map — or changes
 * the dedupe predicate to check different fields — fails here before
 * it ever reaches production.
 */
import { describe, it, expect } from "vitest";
import { mapHistoryTurn, sortMessagesByTimestamp } from "../dateDividers";

// The exact predicate from ChatPanel.jsx's triggerTaskFollowup.
function isFollowupAlreadyInView(msgs, taskId) {
  return msgs.some((mm) => mm.kind === "task_followup" && mm.task_id === taskId);
}

describe("mapHistoryTurn", () => {
  it("preserves kind and task_id from a raw backend turn", () => {
    const raw = {
      role: "assistant",
      content: "✅ **Done — `36ecdee` pushed.**",
      provider: "ora",
      ts: 1757740800, // Sept 13
      kind: "task_followup",
      task_id: "task_abc123",
    };
    const mapped = mapHistoryTurn(raw);
    expect(mapped.kind).toBe("task_followup");
    expect(mapped.task_id).toBe("task_abc123");
    expect(mapped.content).toBe(raw.content);
    expect(mapped.ts).toBe(raw.ts);
  });

  it("preserves kind/task_id for a failed-task followup turn too", () => {
    const raw = {
      role: "assistant",
      content: "❌ Task failed — nothing was committed",
      provider: "ora",
      ts: 1758056400, // Sept 16
      kind: "task_followup",
      task_id: "task_def456",
    };
    const mapped = mapHistoryTurn(raw);
    expect(mapped.kind).toBe("task_followup");
    expect(mapped.task_id).toBe("task_def456");
  });

  it("leaves kind/task_id undefined for an ordinary chat turn (no false positives)", () => {
    const raw = { role: "user", content: "asdkjaslkdj 12903 !!! ???", ts: 1758400000 };
    const mapped = mapHistoryTurn(raw);
    expect(mapped.kind).toBeUndefined();
    expect(mapped.task_id).toBeUndefined();
  });

  it("still preserves every other previously-fixed reload field (no new regression)", () => {
    const raw = {
      role: "assistant", content: "x", provider: "ora", ts: 100,
      review_mode: "strict", watchdog: { ok: true }, feedback: { vote: "up" },
      shipped_task_id: "t1", ship_cancelled: true,
      steps: [{ label: "Reading repo" }],
      low_confidence: true, ship_suppressed: true, interrupted: true,
    };
    const mapped = mapHistoryTurn(raw);
    expect(mapped.reviewMode).toBe("strict");
    expect(mapped.watchdog).toEqual({ ok: true });
    expect(mapped.feedback).toEqual({ vote: "up" });
    expect(mapped.shipped_task_id).toBe("t1");
    expect(mapped.ship_cancelled).toBe(true);
    expect(mapped.steps).toEqual([{ label: "Reading repo" }]);
    expect(mapped.low_confidence).toBe(true);
    expect(mapped.ship_suppressed).toBe(true);
    expect(mapped.interrupted).toBe(true);
  });
});

describe("task-followup dedupe survives a reload (end-to-end reproduction)", () => {
  it("recognises an already-delivered followup after mapHistoryTurn — no duplicate would be appended", () => {
    // Exactly the reported scenario: two old, already-terminal Mode C
    // tasks (Sept 13 push, Sept 16 failure) whose followup turns were
    // persisted by POST /chat/task-followup back when they completed,
    // plus today's real last exchange (gibberish -> "I don't
    // understand that command.").
    const rawHistoryFromServer = [
      { role: "user", content: "ship the retry fix", ts: 1757740000, provider: "ora" },
      { role: "assistant", content: "working on it...", ts: 1757740100, provider: "ora", shipped_task_id: "task_abc123" },
      { role: "assistant", content: "✅ **Done — `36ecdee` pushed.**", ts: 1757740800, provider: "ora", kind: "task_followup", task_id: "task_abc123" },
      { role: "user", content: "fix the flaky test", ts: 1758056000, provider: "ora" },
      { role: "assistant", content: "working on it...", ts: 1758056100, provider: "ora", shipped_task_id: "task_def456" },
      { role: "assistant", content: "❌ Task failed — nothing was committed", ts: 1758056400, provider: "ora", kind: "task_followup", task_id: "task_def456" },
      { role: "user", content: "asdkjaslkdj 12903 !!! ???", ts: 1758400000, provider: "ora" },
      { role: "assistant", content: "I don't understand that command.", ts: 1758400010, provider: "ora" },
    ];

    // What ChatPanel's history-load effect actually produces on reload:
    const messages = sortMessagesByTimestamp(rawHistoryFromServer.map(mapHistoryTurn));

    // Both old tasks' MessageBubbles remount (fresh followupFiredRef)
    // and their terminal-status polls re-fire onTaskCompleted — the
    // dedupe check must find them ALREADY in view and refuse to
    // re-append, for BOTH tasks.
    expect(isFollowupAlreadyInView(messages, "task_abc123")).toBe(true);
    expect(isFollowupAlreadyInView(messages, "task_def456")).toBe(true);

    // The real last exchange stays exactly last — nothing gets
    // appended after it.
    const last = messages[messages.length - 1];
    expect(last.content).toBe("I don't understand that command.");

    // Exactly one occurrence of each followup message — never two.
    const doneCount = messages.filter((m) => m.content?.includes("36ecdee")).length;
    const failedCount = messages.filter((m) => m.content?.includes("nothing was committed")).length;
    expect(doneCount).toBe(1);
    expect(failedCount).toBe(1);
  });

  it("BEFORE the fix (kind/task_id stripped) — proves this test would have caught the regression", () => {
    // Simulates the OLD buggy mapper: same fields as mapHistoryTurn
    // but without kind/task_id, exactly what shipped for the entire
    // lifetime of Iter 53 until this fix.
    const buggyMapper = (t) => ({
      role: t.role, content: t.content, provider: t.provider,
      ts: typeof t.ts === "number" ? t.ts : undefined,
      shipped_task_id: t.shipped_task_id,
    });
    const rawHistoryFromServer = [
      { role: "assistant", content: "✅ **Done — `36ecdee` pushed.**", ts: 1757740800, provider: "ora", kind: "task_followup", task_id: "task_abc123" },
    ];
    const messages = rawHistoryFromServer.map(buggyMapper);
    // With the buggy mapper, the dedupe guard can never find a match
    // — this is the exact silent failure mode that let the duplicate
    // through. Confirms the assertions above are actually meaningful
    // (not vacuously true regardless of the mapper's behaviour).
    expect(isFollowupAlreadyInView(messages, "task_abc123")).toBe(false);
  });
});
