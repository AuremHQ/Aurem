/**
 * src/popup.ts — popup UI glue: setup (API base) → login → capture.
 * Compiled to `popup.js` (see tsconfig.json, rootDir=src/outDir=.)
 * and loaded by popup.html as a plain ES module.
 */
import { getStoredAuth, setApiBase, login, logout, sendContext } from "./api.js";
import type { ApiError } from "./api.js";
import { sanitize } from "./sanitize.js";
import { captureContext } from "./capture.js";

// How long capture.ts listens for console.error/unhandledrejection
// after the click, before this popup auto-sends. See capture.ts's
// docstring for why this can't be retroactive.
const CAPTURE_WINDOW_MS = 4000;

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing #${id}`);
  return el;
}

function show(id: string): void {
  $(id).classList.remove("hidden");
}
function hide(id: string): void {
  $(id).classList.add("hidden");
}

function setStatus(msg: string, kind: "info" | "error" | "success" = "info"): void {
  const el = $("status");
  el.textContent = msg;
  el.className = `status-${kind}`;
  el.classList.remove("hidden");
}

async function refreshView(): Promise<void> {
  const { apiBase, token, email } = await getStoredAuth();
  hide("setup-view");
  hide("login-view");
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
  } catch {
    /* noop — informational only */
  }

  const stored = await chrome.storage.local.get(["aurem_last_session_id"]);
  if (stored.aurem_last_session_id) {
    (($("session-id-input")) as HTMLInputElement).value = stored.aurem_last_session_id;
  }
}

$("save-api-base-btn").addEventListener("click", async () => {
  const val = ($("api-base-input") as HTMLInputElement).value.trim();
  if (!val) return;
  await setApiBase(val);
  await refreshView();
});

$("login-btn").addEventListener("click", async () => {
  const email = ($("email-input") as HTMLInputElement).value.trim();
  const password = ($("password-input") as HTMLInputElement).value;
  const errEl = $("login-error");
  errEl.classList.add("hidden");
  if (!email || !password) return;

  const { apiBase } = await getStoredAuth();
  if (!apiBase) return;

  try {
    await login(apiBase, email, password);
    await refreshView();
  } catch (e) {
    errEl.textContent = (e as Error)?.message || "Login failed";
    errEl.classList.remove("hidden");
  }
});

$("logout-btn").addEventListener("click", async () => {
  await logout();
  await refreshView();
});

$("capture-btn").addEventListener("click", async () => {
  const sessionId = ($("session-id-input") as HTMLInputElement).value.trim();
  if (!sessionId) {
    setStatus(
      "Paste the chat session ID first — copy it from the small badge in AUREM's top bar.",
      "error"
    );
    return;
  }
  await chrome.storage.local.set({ aurem_last_session_id: sessionId });

  const { apiBase, token } = await getStoredAuth();
  if (!apiBase || !token) {
    await refreshView();
    return;
  }

  const btn = $("capture-btn") as HTMLButtonElement;
  btn.disabled = true;
  let tickHandle: number | undefined;

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("No active tab found");

    let secondsLeft = Math.round(CAPTURE_WINDOW_MS / 1000);
    setStatus(
      `Capturing… listening for console errors (${secondsLeft}s) — reproduce the issue now if you want it captured`,
      "info"
    );
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
    if (tickHandle) window.clearInterval(tickHandle);

    const result = results?.[0]?.result;
    if (!result) {
      throw new Error(
        "Capture returned no data — this page may block content scripts (e.g. chrome:// or the Chrome Web Store)."
      );
    }

    setStatus("Sanitizing…", "info");
    const sanitizedText = sanitize(result.visibleText).text;
    const sanitizedErrors = result.consoleErrors.map((e: string) => sanitize(e).text);

    setStatus("Sending to ORA…", "info");
    await sendContext(apiBase, token, {
      session_id: sessionId,
      url: result.url,
      title: result.title,
      visible_text: sanitizedText,
      console_errors: sanitizedErrors,
      network_metrics: result.networkMetrics,
      captured_at: new Date().toISOString(),
    });

    setStatus("\u2713 Sent to ORA — check that chat session.", "success");
  } catch (e) {
    if (tickHandle) window.clearInterval(tickHandle);
    const err = e as ApiError;
    if (err?.status === 401) {
      await logout();
      setStatus("Your session expired — please sign in again.", "error");
      await refreshView();
    } else {
      setStatus(err?.message || "Failed to send context", "error");
    }
  } finally {
    btn.disabled = false;
  }
});

refreshView();
