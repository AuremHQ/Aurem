/**
 * src/ring-buffer.ts — pure helpers for the Part VII fix 4(d) console
 * error ring buffer. No chrome.* APIs here, so this module is shared
 * by the background service worker (`background.ts`, which owns the
 * per-tab buffers) and the popup (`popup.ts`, which merges buffered +
 * live errors) — and is unit-testable in plain Node.
 *
 * The content script (`content-ring-buffer.ts`) deliberately does NOT
 * import this file: manifest content scripts are classic scripts, not
 * ES modules, so that file is self-contained and only forwards raw
 * errors to the background, where the caps below are applied.
 */

export interface RingBufferEntry {
  /** Error text, already truncated to RING_BUFFER_MAX_CHARS. */
  text: string;
  /** `Date.now()` at capture time, for chronological merge. */
  ts: number;
}

/** Max errors kept per tab — same order as capture.ts's 20 sent, with
 *  headroom so the merge can prefer the most recent ones. */
export const RING_BUFFER_MAX_ENTRIES = 50;
/** Max chars per error — matches capture.ts's 600-char slice. */
export const RING_BUFFER_MAX_CHARS = 600;
/** Max tabs with a live buffer — bounds service-worker memory. */
export const RING_BUFFER_MAX_TABS = 10;
/** Max errors sent to the server in one capture — matches the
 *  existing server cap (`_MAX_ERROR_ITEMS = 20`). */
export const RING_BUFFER_MAX_SEND = 20;

/** Truncate one error the same way capture.ts does. */
export function truncateError(text: string): string {
  return String(text).slice(0, RING_BUFFER_MAX_CHARS);
}

/** Append an entry, dropping the oldest when the ring is full.
 *  Returns the (possibly new) buffer — pure, no mutation of the input. */
export function pushToRing(
  buffer: RingBufferEntry[],
  entry: RingBufferEntry
): RingBufferEntry[] {
  const next = [
    ...buffer,
    { text: truncateError(entry.text), ts: entry.ts },
  ];
  return next.length > RING_BUFFER_MAX_ENTRIES
    ? next.slice(next.length - RING_BUFFER_MAX_ENTRIES)
    : next;
}

/** Merge pre-click buffered errors with the live-window errors from
 *  capture.ts into the list sent to the server.
 *
 *  - Chronological: buffered entries carry `ts`; live errors happened
 *    after the click, so they sort after all buffered ones.
 *  - Dedupe: an identical message keeps its FIRST (earliest)
 *    occurrence — a page that logs the same error 50 times does not
 *    flood the 20 slots.
 *  - Cap: at most RING_BUFFER_MAX_SEND, keeping the MOST RECENT ones
 *    when over cap, so a flood of old buffered errors cannot crowd
 *    out the errors from the live window the founder just triggered.
 */
export function mergeBufferedErrors(
  buffered: RingBufferEntry[],
  live: string[],
  maxSend: number = RING_BUFFER_MAX_SEND
): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  const sortedBuffered = [...buffered].sort((a, b) => a.ts - b.ts);
  for (const entry of sortedBuffered) {
    const text = truncateError(entry.text);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    ordered.push(text);
  }
  for (const err of live) {
    const text = truncateError(err);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    ordered.push(text);
  }
  return ordered.length > maxSend
    ? ordered.slice(ordered.length - maxSend)
    : ordered;
}
