/**
 * utils/dateDividers.js — WhatsApp/Telegram-style chat date dividers.
 *
 * Pure helpers only (no React) so they're trivially unit-testable and
 * reusable regardless of whether the message list is virtualized.
 *
 * Data source: each message may carry a `ts` field — a Unix epoch in
 * SECONDS (matches the backend's `chat_sessions.turns[].ts`, written
 * via `time.time()` in services/chat_helpers.py). We derive the
 * CALENDAR DAY from the message's LOCAL (browser) timezone, never UTC.
 *
 * Missing timestamp rule: if a message has no/zero `ts`, it groups
 * with the PREVIOUS message's day — never a spurious divider, never
 * a crash.
 */
import dayjs from "dayjs";
import { interactionFields } from "./interactionState";

/**
 * Returns a stable "YYYY-MM-DD" key for the LOCAL calendar day of a
 * Unix-epoch-seconds timestamp, or null if the timestamp is
 * missing/falsy/invalid.
 */
export function dayKeyOf(ts) {
  if (!ts || typeof ts !== "number" || !Number.isFinite(ts)) return null;
  const d = dayjs(ts * 1000);
  if (!d.isValid()) return null;
  return d.format("YYYY-MM-DD");
}

/**
 * Human label for a day key: "Today", "Yesterday", or the full
 * "D Month YYYY" format (e.g. "15 September 2026").
 */
export function dividerLabelFor(dayKey) {
  if (!dayKey) return "";
  const today = dayjs().format("YYYY-MM-DD");
  const yesterday = dayjs().subtract(1, "day").format("YYYY-MM-DD");
  if (dayKey === today) return "Today";
  if (dayKey === yesterday) return "Yesterday";
  return dayjs(dayKey, "YYYY-MM-DD").format("D MMMM YYYY");
}

/**
 * Given the full messages array, returns a Map from message INDEX to
 * the divider label that should render immediately BEFORE that
 * message — or undefined if no divider belongs there.
 *
 * Missing-ts messages inherit the running "current day" (i.e. they
 * group with whatever day preceded them) so they never trigger a
 * divider and never desync the ones that follow.
 */
export function computeDateDividers(messages) {
  const dividers = new Map();
  let runningDayKey = null;
  for (let i = 0; i < (messages || []).length; i++) {
    const m = messages[i];
    const key = dayKeyOf(m?.ts);
    const effectiveKey = key || runningDayKey; // missing ts -> inherit
    if (effectiveKey && effectiveKey !== runningDayKey) {
      dividers.set(i, dividerLabelFor(effectiveKey));
    }
    if (effectiveKey) runningDayKey = effectiveKey;
  }
  return dividers;
}

/**
 * 2026-09-20 — root-fix: extracted from ChatPanel's `GET /chat/history`
 * hydration so it's testable as real behaviour, not just source text.
 *
 * Maps one raw backend turn (`chat_sessions.turns[i]`, as returned by
 * `GET /chat/history`) into the shape ChatPanel keeps in `messages`
 * state. MUST preserve every field some other part of the app reads
 * back off a message object — this list has grown by bug report
 * every time one was silently dropped (see the low_confidence/
 * ship_suppressed/interrupted comments below, all the same lesson).
 *
 * `kind` + `task_id` are the fields at the center of this fix: Iter 53
 * (POST /chat/task-followup) persists them on a task-completion turn
 * SPECIFICALLY so ChatPanel's own client-side dedupe check
 * (`msgs.some(mm => mm.kind === "task_followup" && mm.task_id ===
 * taskId)`) can recognise "this wrap-up is already in view" after a
 * reload and skip re-appending it. This mapper never carried those
 * two fields over, so after ANY reload the dedupe check silently
 * always failed: the old task's MessageBubble remounts, its terminal-
 * status poll refires onTaskCompleted, the (idempotent-on-the-
 * backend, but client-blind) followup call returns the same cached
 * text, and — finding no match — a duplicate with no `ts` gets pushed
 * onto the end of the array, landing after whatever is currently the
 * last message regardless of when it really happened. Not a fix that
 * regressed — the dedupe check has depended on these two fields since
 * the day it was written; this mapper just never supplied them.
 */
export function mapHistoryTurn(t) {
  return {
    role: t.role,
    content: t.content,
    provider: t.provider,
    ts: typeof t.ts === "number" ? t.ts : undefined,
    reviewMode: t.review_mode || null,
    watchdog: t.watchdog,
    feedback: t.feedback,
    shipped_task_id: t.shipped_task_id,
    ship_cancelled: t.ship_cancelled || false,
    steps: Array.isArray(t.steps) && t.steps.length > 0 ? t.steps : undefined,
    low_confidence: t.low_confidence || false,
    ship_suppressed: t.ship_suppressed || false,
    interrupted: t.interrupted || false,
    kind: t.kind,
    task_id: t.task_id,
    awaitingScopeConfirmation: !!t.awaiting_scope_confirmation,
    scopeConfirmationProposalId: t.scope_confirmation_proposal_id || null,
    scopeConfirmationPrompt: t.scope_confirmation_prompt || null,
    scopeExecution: t.scope_execution || null,
    ...interactionFields(t),
  };
}

/**
 * 2026-09-11 — real bug repro: a Loop task paused for human approval
 * (plan/ship gate) can be resumed hours/days after it was submitted.
 * Its chat-turn pair only gets persisted (services/loop_engine.py
 * `_persist_chat_turns`) once the loop reaches a TERMINAL state, so
 * that write can land in `chat_sessions.turns` well AFTER other,
 * genuinely newer turns were already appended — the raw array's
 * PUSH order stops matching real chronological (`ts`) order. Since
 * nothing sorts the array before rendering, the stale turn visually
 * landed at the very bottom (the "most recent" slot) instead of its
 * real chronological position, and desynced the date dividers above.
 *
 * Stable-sorts a message list by `ts` ascending. A message with a
 * missing/invalid `ts`, or one that TIES with another (the user+
 * assistant turn of one exchange share the same `ts`), falls back to
 * its ORIGINAL array position — never aggressively reordered on
 * incomplete data, mirroring computeDateDividers' own "missing ts
 * inherits" philosophy above.
 */
export function sortMessagesByTimestamp(messages) {
  return (messages || [])
    .map((m, i) => ({ m, i }))
    .sort((a, b) => {
      const at = typeof a.m?.ts === "number" ? a.m.ts : null;
      const bt = typeof b.m?.ts === "number" ? b.m.ts : null;
      if (at !== null && bt !== null && at !== bt) return at - bt;
      return a.i - b.i;
    })
    .map(({ m }) => m);
}
