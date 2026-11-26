/**
 * src/popup.ts — popup UI glue: setup (API base) → login → capture.
 * Compiled to `popup.js` (see tsconfig.json, rootDir=src/outDir=.)
 * and loaded by popup.html as a plain ES module.
 */
import { getStoredAuth, setApiBase, login, verifyMfa, logout } from "./api.js";
// Part VII fix 4(b): bridge-token send (separate import line — the
// part7 lane's import above gains `verifyMfa` for 4(a); keep the two
// hunks apart so the combiner can fold them without a conflict).
import { sendContextViaBridge } from "./api.js";
// Part VII fix 4(d): merge pre-click buffered errors with the live
// window (ring-buffer.ts is shared with background.ts).
import { mergeBufferedErrors } from "./ring-buffer.js";
import { sanitize } from "./sanitize.js";
import { captureContext } from "./capture.js";
// Part VII fix 4(a) — pending MFA challenge between the two login
// legs. Memory only: the 5-minute mfa_pending token is never written
// to chrome.storage.
let pendingMfa = null;
let mfaUseBackup = false;
// How long capture.ts listens for console.error/unhandledrejection
// after the click, before this popup auto-sends. Pre-click errors no
// longer depend on this window alone: the ring buffer
// (content-ring-buffer.ts + background.ts) holds errors from page
// load, and they are merged in below — the window is now only for
// errors the founder triggers by reproducing the issue.
const CAPTURE_WINDOW_MS = 4000;
/** Part VII fix 4(d): ask the background service worker for this
 *  tab's pre-click buffered errors. Any failure (worker not ready,
 *  tab not covered by the content script, CSP-blocked hook) degrades
 *  to "no buffered errors" — the live window still runs. */
async function getBufferedErrors(tabId) {
    try {
        const res = await chrome.runtime.sendMessage({
            type: "aurem-get-buffered-errors",
            tabId,
        });
        const entries = res?.errors;
        return Array.isArray(entries) ? entries : [];
    }
    catch {
        return [];
    }
}
function $(id) {
    const el = document.getElementById(id);
    if (!el)
        throw new Error(`Missing #${id}`);
    return el;
}
function show(id) {
    $(id).classList.remove("hidden");
}
function hide(id) {
    $(id).classList.add("hidden");
}
function setStatus(msg, kind = "info") {
    const el = $("status");
    el.textContent = msg;
    el.className = `status-${kind}`;
    el.classList.remove("hidden");
}
async function refreshView() {
    const { apiBase, token, email } = await getStoredAuth();
    hide("setup-view");
    hide("login-view");
    hide("mfa-view");
    hide("main-view");
    if (!apiBase) {
        show("setup-view");
        return;
    }
    if (!token) {
        show("login-view");
        return;
    }
    $("account-email").textContent = email || "";
    show("main-view");
    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        $("tab-info").textContent = tab?.url ? `Active tab: ${tab.url}` : "No active tab";
    }
    catch {
        /* noop — informational only */
    }
    const stored = await chrome.storage.local.get(["aurem_last_session_id"]);
    if (stored.aurem_last_session_id) {
        ($("session-id-input")).value = stored.aurem_last_session_id;
    }
}
$("save-api-base-btn").addEventListener("click", async () => {
    const val = $("api-base-input").value.trim();
    if (!val)
        return;
    await setApiBase(val);
    await refreshView();
});
$("login-btn").addEventListener("click", async () => {
    const email = $("email-input").value.trim();
    const password = $("password-input").value;
    const errEl = $("login-error");
    errEl.classList.add("hidden");
    if (!email || !password)
        return;
    const { apiBase } = await getStoredAuth();
    if (!apiBase)
        return;
    try {
        const result = await login(apiBase, email, password);
        if (result.mfaRequired && result.mfaToken) {
            // 4(a) — MFA account: second leg. Keep the challenge in memory
            // only and ask for the TOTP code instead of failing the login.
            pendingMfa = { apiBase, email, mfaToken: result.mfaToken };
            mfaUseBackup = false;
            $("mfa-code-input").value = "";
            $("mfa-code-input").placeholder = "123456";
            $("mfa-error").classList.add("hidden");
            hide("login-view");
            show("mfa-view");
            $("mfa-code-input").focus();
            return;
        }
        await refreshView();
    }
    catch (e) {
        errEl.textContent = e?.message || "Login failed";
        errEl.classList.remove("hidden");
    }
});
$("mfa-verify-btn").addEventListener("click", async () => {
    const code = $("mfa-code-input").value.trim();
    const errEl = $("mfa-error");
    errEl.classList.add("hidden");
    if (!code || !pendingMfa)
        return;
    try {
        await verifyMfa(pendingMfa.apiBase, pendingMfa.email, pendingMfa.mfaToken, code, mfaUseBackup);
        pendingMfa = null;
        $("mfa-code-input").value = "";
        await refreshView();
    }
    catch (e) {
        errEl.textContent = e?.message || "2FA verification failed";
        errEl.classList.remove("hidden");
    }
});
$("mfa-backup-toggle").addEventListener("click", () => {
    mfaUseBackup = !mfaUseBackup;
    const input = $("mfa-code-input");
    input.placeholder = mfaUseBackup ? "Backup code" : "123456";
    input.value = "";
    $("mfa-backup-toggle").textContent = mfaUseBackup
        ? "Use the authenticator app instead"
        : "Use a backup code instead";
});
$("mfa-cancel-btn").addEventListener("click", async () => {
    pendingMfa = null;
    await refreshView();
});
$("logout-btn").addEventListener("click", async () => {
    await logout();
    await refreshView();
});
$("capture-btn").addEventListener("click", async () => {
    const sessionId = $("session-id-input").value.trim();
    if (!sessionId) {
        setStatus("Paste the chat session ID first — copy it from the small badge in AUREM's top bar.", "error");
        return;
    }
    await chrome.storage.local.set({ aurem_last_session_id: sessionId });
    const { apiBase, token } = await getStoredAuth();
    if (!apiBase || !token) {
        await refreshView();
        return;
    }
    const btn = $("capture-btn");
    btn.disabled = true;
    let tickHandle;
    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab?.id)
            throw new Error("No active tab found");
        // 4(d) — fetch the pre-click buffered errors BEFORE the live
        // window starts, so they are not confused with errors from the
        // reproduction window below.
        const bufferedErrors = await getBufferedErrors(tab.id);
        let secondsLeft = Math.round(CAPTURE_WINDOW_MS / 1000);
        setStatus(`Capturing… listening for console errors (${secondsLeft}s) — reproduce the issue now if you want it captured` +
            (bufferedErrors.length > 0
                ? ` (${bufferedErrors.length} earlier error(s) already buffered)`
                : ""), "info");
        tickHandle = window.setInterval(() => {
            secondsLeft -= 1;
            if (secondsLeft > 0) {
                setStatus(`Capturing… listening for console errors (${secondsLeft}s)`, "info");
            }
        }, 1000);
        const results = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: captureContext,
            args: [CAPTURE_WINDOW_MS],
        });
        if (tickHandle)
            window.clearInterval(tickHandle);
        const result = results?.[0]?.result;
        if (!result) {
            throw new Error("Capture returned no data — this page may block content scripts (e.g. chrome:// or the Chrome Web Store).");
        }
        setStatus("Sanitizing…", "info");
        const sanitizedText = sanitize(result.visibleText).text;
        // 4(d) — merge pre-click buffered errors with the live window's,
        // chronological + deduped, capped at 20, then sanitize the merged
        // list (buffered errors are page text too — same treatment).
        const mergedErrors = mergeBufferedErrors(bufferedErrors, result.consoleErrors);
        const sanitizedErrors = mergedErrors.map((e) => sanitize(e).text);
        setStatus("Sending to ORA…", "info");
        // 4(b) — send with a 15-minute scoped bridge token, not the
        // session JWT. `token` here is still the session token; it is
        // only used to mint the bridge token inside sendContextViaBridge.
        await sendContextViaBridge(apiBase, token, {
            session_id: sessionId,
            url: result.url,
            title: result.title,
            visible_text: sanitizedText,
            console_errors: sanitizedErrors,
            network_metrics: result.networkMetrics,
            captured_at: new Date().toISOString(),
        });
        setStatus("\u2713 Sent to ORA — check that chat session.", "success");
    }
    catch (e) {
        if (tickHandle)
            window.clearInterval(tickHandle);
        const err = e;
        if (err?.status === 401) {
            await logout();
            setStatus("Your session expired — please sign in again.", "error");
            await refreshView();
        }
        else {
            setStatus(err?.message || "Failed to send context", "error");
        }
    }
    finally {
        btn.disabled = false;
    }
});
refreshView();
