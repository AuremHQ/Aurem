"use strict";
/**
 * src/content-ring-buffer.ts — Part VII fix 4(d): capture console
 * errors from PAGE LOAD, so the founder no longer has to reproduce
 * an issue inside the 4-second post-click window in capture.ts.
 *
 * Architecture (why two scripts in one file):
 *  - This file runs as a Manifest V3 content script in an ISOLATED
 *    world: it CANNOT see the page's own `console.error` — Chrome
 *    gives each world its own console object. Wrapping console here
 *    would only catch this script's own errors.
 *  - So this file injects a tiny hook into the page's MAIN world
 *    (a <script> element, installed at document_start before page
 *    scripts run). The hook wraps the page's real `console.error`
 *    and forwards each error to this script via `window.postMessage`.
 *  - This script forwards every error to the background service
 *    worker (`background.ts`), which owns the bounded per-tab ring
 *    buffer. It keeps NO buffer itself: one owner, no drift.
 *
 * KNOWN LIMITS (stated plainly, not hidden):
 *  - A page with a strict Content-Security-Policy (script-src
 *    without 'unsafe-inline') BLOCKS the injected <script> element,
 *    so the hook never installs and no pre-click errors are captured
 *    for that page. The post-click live window in capture.ts still
 *    works there. There is no CSP-proof injection from a content
 *    script; `chrome.scripting` MAIN-world injection from the
 *    background would bypass CSP but needs broader host permissions
 *    and a tab-update listener — a follow-up if CSP pages matter.
 *  - Errors logged before this script runs at document_start (a
 *    vanishingly small window — document_start fires before page
 *    scripts) are still missed.
 *  - Nothing is persisted: errors live in the background worker's
 *    memory + chrome.storage.session (cleared on browser close).
 *    Closing the tab drops its buffer.
 *
 * Self-contained by necessity: manifest content scripts are classic
 * scripts, not ES modules, so this file has NO imports/exports.
 * Compiled to `src/content-ring-buffer.js` by `tsc -p .`.
 */
(function () {
    // Guard against double install (e.g. SPA re-injection).
    const w = window;
    if (w.__auremRingBufferInstalled)
        return;
    w.__auremRingBufferInstalled = true;
    // --- MAIN-world hook, injected as a <script> element. Runs in the
    // page's own JS world, so it sees the page's real console.error.
    // Kept as a string (no `</script>` sequence inside — the element is
    // created via textContent, never via innerHTML parsing).
    const HOOK_SOURCE = "(function(){\n" +
        "if(window.__auremRingBufferHookInstalled)return;\n" +
        "window.__auremRingBufferHookInstalled=true;\n" +
        "function post(text){try{window.postMessage({source:'aurem-ring-buffer',text:String(text).slice(0,600),ts:Date.now()},'*');}catch(e){}}\n" +
        "var origError=console.error;\n" +
        "console.error=function(){try{var msg=[];for(var i=0;i<arguments.length;i++){var a=arguments[i];msg.push(typeof a==='string'?a:JSON.stringify(a));}post(msg.join(' '));}catch(e){}return origError.apply(console,arguments);};\n" +
        "window.addEventListener('error',function(e){try{post(String(e.message||e));}catch(err){}});\n" +
        "window.addEventListener('unhandledrejection',function(e){try{post('Unhandled rejection: '+String(e.reason));}catch(err){}});\n" +
        "})();";
    try {
        const script = document.createElement("script");
        script.textContent = HOOK_SOURCE;
        (document.documentElement || document.head || document.body).appendChild(script);
        script.remove();
    }
    catch {
        // CSP or a page without a document element: the hook cannot
        // install. Pre-click capture is unavailable here; the live
        // post-click window in capture.ts is the fallback.
    }
    // --- Isolated-world forwarder: page hook -> background worker.
    window.addEventListener("message", (event) => {
        try {
            if (event.source !== window)
                return;
            const data = event.data;
            if (!data || data.source !== "aurem-ring-buffer")
                return;
            if (typeof data.text !== "string" || !data.text)
                return;
            chrome.runtime.sendMessage({
                type: "aurem-ring-buffer-push",
                text: data.text.slice(0, 600),
                ts: typeof data.ts === "number" ? data.ts : Date.now(),
            });
        }
        catch {
            // Never let the forwarder break the page. A dropped error is
            // preferable to an exception in a message listener.
        }
    });
})();
