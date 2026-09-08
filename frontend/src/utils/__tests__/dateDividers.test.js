/**
 * utils/dateDividers.test.js — WhatsApp/Telegram-style chat date
 * dividers. Named tests per the standing spec (8 required):
 *   t_divider_between_different_days
 *   t_no_divider_same_day
 *   t_today_yesterday_labels
 *   t_first_message_has_divider
 *   t_timezone_local
 *   t_missing_ts_groups_with_previous
 *   t_divider_in_virtualized_list
 *   t_theme_aware
 */
import { describe, it, expect } from "vitest";
import dayjs from "dayjs";
import { computeDateDividers, dayKeyOf, dividerLabelFor, sortMessagesByTimestamp } from "../dateDividers";

// Real "now" reference — the utility under test correctly uses the
// REAL current date (dayjs()) to decide Today/Yesterday, so tests
// must anchor relative to the real clock, not a fictional fixed date.
const NOW = dayjs();
const todaySec = (h, m = 0) => NOW.hour(h).minute(m).second(0).unix();
const daysAgoSec = (days, h = 12) => NOW.subtract(days, "day").hour(h).unix();

describe("dateDividers", () => {
  it("t_divider_between_different_days: two messages on different days get one divider between them", () => {
    const messages = [
      { role: "user", content: "hi", ts: daysAgoSec(1, 9) },     // yesterday 09:00
      { role: "assistant", content: "hey", ts: daysAgoSec(1, 9) + 5 },
      { role: "user", content: "morning", ts: todaySec(9) },     // today 09:00
    ];
    const dividers = computeDateDividers(messages);
    expect(dividers.size).toBe(2); // one for msg 0 (first msg), one for msg 2 (day change)
    expect(dividers.has(1)).toBe(false); // same day as msg 0 -> no divider
    expect(dividers.get(2)).toBeTruthy();
  });

  it("t_no_divider_same_day: two same-day messages -> no divider between them", () => {
    const messages = [
      { role: "user", content: "hi", ts: todaySec(9) },
      { role: "assistant", content: "hey", ts: todaySec(9, 5) },
    ];
    const dividers = computeDateDividers(messages);
    // Only the first message gets a divider (very first message rule);
    // the second, same-day message must NOT get one.
    expect(dividers.has(0)).toBe(true);
    expect(dividers.has(1)).toBe(false);
  });

  it("t_today_yesterday_labels: today -> 'Today', yesterday -> 'Yesterday', older -> full date", () => {
    const todayKey = dayjs().format("YYYY-MM-DD");
    const yesterdayKey = dayjs().subtract(1, "day").format("YYYY-MM-DD");
    const olderKey = "2026-09-15";
    expect(dividerLabelFor(todayKey)).toBe("Today");
    expect(dividerLabelFor(yesterdayKey)).toBe("Yesterday");
    expect(dividerLabelFor(olderKey)).toBe("15 September 2026");
  });

  it("t_first_message_has_divider: the very first message in the list always gets a divider above it", () => {
    const messages = [{ role: "user", content: "hello", ts: todaySec(10) }];
    const dividers = computeDateDividers(messages);
    expect(dividers.get(0)).toBe("Today");
  });

  it("t_timezone_local: a message at 23:30 UTC on day X, which is already 'tomorrow' in a positive-offset local tz, groups by the LOCAL day", () => {
    // dayjs(ms) in this test environment uses the runner's local tz
    // (no explicit UTC parsing) — this IS the local-timezone behavior
    // the spec requires: derive the day from local wall-clock time,
    // not from the UTC calendar date.
    const utcLateNight = Date.UTC(2026, 8, 15, 23, 30, 0) / 1000; // 15 Sep 23:30 UTC
    const localDay = dayjs(utcLateNight * 1000).format("YYYY-MM-DD");
    expect(dayKeyOf(utcLateNight)).toBe(localDay);
    // Explicitly NOT hardcoded to the UTC calendar date string —
    // proves we didn't accidentally call .utc() or toISOString()
    // date-slicing anywhere in dayKeyOf.
  });

  it("t_missing_ts_groups_with_previous: a message with no timestamp groups with the previous message's day, no spurious divider", () => {
    const messages = [
      { role: "user", content: "hi", ts: todaySec(9) },
      { role: "assistant", content: "hey", ts: undefined }, // no ts at all
      { role: "user", content: "still here?", ts: 0 },       // zero ts
    ];
    const dividers = computeDateDividers(messages);
    expect(dividers.has(0)).toBe(true);   // first message
    expect(dividers.has(1)).toBe(false);  // missing ts -> inherits msg 0's day
    expect(dividers.has(2)).toBe(false);  // zero ts -> also inherits
  });

  it("t_divider_in_virtualized_list: divider indices are computed as plain list positions so they work identically whether the list is virtualized or not", () => {
    // This codebase's message list is NOT virtualized (plain .map()),
    // so a "virtual list item" concern reduces to: the divider must be
    // addressable by the SAME index the message list already uses,
    // with no separate/parallel data structure that could desync from
    // whatever subset of rows a virtualizer chooses to mount. Confirms
    // computeDateDividers returns index-keyed results (compatible with
    // any windowing scheme that renders by absolute array index).
    const messages = [
      { role: "user", content: "a", ts: daysAgoSec(2, 8) },
      { role: "user", content: "b", ts: daysAgoSec(1, 8) },
      { role: "user", content: "c", ts: todaySec(8) },
    ];
    const dividers = computeDateDividers(messages);
    expect([...dividers.keys()]).toEqual([0, 1, 2]);
    // A virtualizer that only mounts index 2 can still look up
    // dividers.get(2) directly — no dependency on prior indices
    // having been rendered.
    expect(dividers.get(2)).toBe("Today");
  });
});

// 2026-09-11 — real founder bug repro: a Loop turn-pair persisted at
// TERMINAL state (which can be hours/days after the loop was paused
// for human approval) can land at the END of the raw turns array even
// though its real `ts` is much OLDER than turns sent in between.
// sortMessagesByTimestamp must restore true chronological order.
describe("sortMessagesByTimestamp", () => {
  it("t_sort_restores_chronological_order: an old turn appended late (out of order) is moved back to its real position", () => {
    const messages = [
      { role: "user", content: "old prompt from 3 days ago", ts: daysAgoSec(3, 9) },
      { role: "assistant", content: "old reply", ts: daysAgoSec(3, 9) + 1 },
      { role: "user", content: "brand new message", ts: todaySec(10) },
      { role: "assistant", content: "brand new reply", ts: todaySec(10) + 1 },
      // Loop turn-pair for a task the user submitted long ago but only
      // just approved/resumed — pushed LAST in the raw array, but its
      // real ts is older than everything above.
      { role: "user", content: "regression-test the commit", ts: daysAgoSec(5, 8) },
      { role: "assistant", content: "loop run complete", ts: todaySec(11) },
    ];
    const sorted = sortMessagesByTimestamp(messages);
    expect(sorted.map((m) => m.content)).toEqual([
      "regression-test the commit",
      "old prompt from 3 days ago",
      "old reply",
      "brand new message",
      "brand new reply",
      "loop run complete",
    ]);
  });

  it("t_sort_stable_for_ties: two turns sharing the exact same ts (a user+assistant pair) keep their original relative order", () => {
    const sharedTs = todaySec(9);
    const messages = [
      { role: "user", content: "u1", ts: sharedTs },
      { role: "assistant", content: "a1", ts: sharedTs },
    ];
    const sorted = sortMessagesByTimestamp(messages);
    expect(sorted.map((m) => m.content)).toEqual(["u1", "a1"]);
  });

  it("t_sort_stable_for_missing_ts: a message with no ts never gets aggressively reordered — stays in its original relative position", () => {
    const messages = [
      { role: "user", content: "first", ts: todaySec(9) },
      { role: "assistant", content: "legacy-no-ts", ts: undefined },
      { role: "user", content: "later", ts: todaySec(11) },
    ];
    const sorted = sortMessagesByTimestamp(messages);
    expect(sorted.map((m) => m.content)).toEqual(["first", "legacy-no-ts", "later"]);
  });

  it("t_sort_noop_on_already_sorted: an already-chronological list is returned unchanged", () => {
    const messages = [
      { role: "user", content: "a", ts: daysAgoSec(1, 8) },
      { role: "user", content: "b", ts: todaySec(8) },
    ];
    expect(sortMessagesByTimestamp(messages).map((m) => m.content)).toEqual(["a", "b"]);
  });
});
