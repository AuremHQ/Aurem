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
