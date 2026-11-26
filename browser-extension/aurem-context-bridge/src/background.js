/**
 * src/background.ts — Part VII fix 4(d): the service-worker store for
 * the pre-click console-error ring buffer.
 *
 * The content script (`content-ring-buffer.ts`) forwards every
 * console.error from page load; this worker owns the buffers, one per
 * tabId, bounded by the caps in `ring-buffer.ts` (50 entries x 600
 * chars, 10 tabs). The popup asks this worker — NOT the page — for a
 * tab's buffered errors before the live capture runs (`popup.ts`).
 *
 * Service-worker suspension: MV3 suspends this worker after ~30 s
 * idle, which would wipe in-memory buffers. So every push is also
 * written through to `chrome.storage.session` (survives suspend,
 * cleared on browser close — nothing about a founder's browsing is
 * persisted to disk beyond the session) and reloaded on worker start.
 * A tab's buffer is dropped when the tab closes or navigates (a new
 * page load starts a fresh buffer; the content script re-injects).
 * Nothing here ever leaves the browser except via the popup's
 * explicit send — the worker itself makes no network calls.
 *
 * Compiled to `src/background.js` by `tsc -p .` (module worker, see
 * manifest `"type": "module"`).
 */
import { pushToRing, RING_BUFFER_MAX_TABS, } from "./ring-buffer.js";
const SESSION_KEY = "aurem_ring_buffers";
const buffers = new Map();
async function loadFromSession() {
    try {
        const data = await chrome.storage.session.get([SESSION_KEY]);
        const stored = data[SESSION_KEY];
        if (!stored || typeof stored !== "object")
            return;
        for (const [key, entries] of Object.entries(stored)) {
            const tabId = Number(key);
            if (!Number.isInteger(tabId) || !Array.isArray(entries))
                continue;
            buffers.set(tabId, { entries, lastPush: Date.now() });
        }
    }
    catch {
        // Session storage unavailable — memory-only mode. Buffers still
        // work until the worker suspends; the popup degrades to live-only.
    }
}
async function persist() {
    try {
        const obj = {};
        buffers.forEach((buf, tabId) => {
            obj[String(tabId)] = buf.entries;
        });
        await chrome.storage.session.set({ [SESSION_KEY]: obj });
    }
    catch {
        // Best-effort only; the in-memory buffer is still authoritative
        // until the worker suspends.
    }
}
function pruneTabs() {
    while (buffers.size > RING_BUFFER_MAX_TABS) {
        let oldestId;
        let oldestPush = Infinity;
        buffers.forEach((buf, tabId) => {
            if (buf.lastPush < oldestPush) {
                oldestPush = buf.lastPush;
                oldestId = tabId;
            }
        });
        if (oldestId === undefined)
            return;
        buffers.delete(oldestId);
    }
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "aurem-ring-buffer-push") {
        const tabId = sender.tab?.id;
        if (typeof tabId !== "number")
            return;
        if (typeof message.text !== "string" || !message.text)
            return;
        const current = buffers.get(tabId)?.entries ?? [];
        buffers.set(tabId, {
            entries: pushToRing(current, {
                text: message.text,
                ts: typeof message.ts === "number" ? message.ts : Date.now(),
            }),
            lastPush: Date.now(),
        });
        pruneTabs();
        void persist();
        return;
    }
    if (message?.type === "aurem-get-buffered-errors") {
        // Async reply (may need a session-storage reload first) — return
        // true to keep the message channel open.
        void (async () => {
            const tabId = message.tabId;
            if (typeof tabId === "number" && !buffers.has(tabId)) {
                await loadFromSession();
            }
            const entries = typeof tabId === "number" ? buffers.get(tabId)?.entries ?? [] : [];
            sendResponse({ errors: entries });
        })();
        return true;
    }
});
chrome.tabs.onRemoved.addListener((tabId) => {
    if (buffers.delete(tabId))
        void persist();
});
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    // Navigation starts a fresh page (and a fresh buffer — the content
    // script re-injects at document_start). Drop the old page's errors
    // so a capture never mixes two pages' errors.
    if (changeInfo.status === "loading") {
        if (buffers.delete(tabId))
            void persist();
    }
});
chrome.runtime.onStartup.addListener(() => {
    void loadFromSession();
});
// Warm the cache on worker start (covers suspend/resume, where the
// worker restarts with empty memory but session storage intact).
void loadFromSession();
