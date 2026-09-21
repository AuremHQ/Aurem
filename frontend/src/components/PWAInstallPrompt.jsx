/**
 * PWAInstallPrompt.jsx — friendly install banner (2026-09-21 round 2:
 * now fires for first-time anonymous visitors too, not just right
 * after login).
 *
 * Hooks the browser's `beforeinstallprompt` event and surfaces it as a
 * branded card the user can tap to install AUREM as a PWA. Pairs with
 * Iter 80's service-worker + manifest work. Mounted ONCE, globally, in
 * App.jsx (outside the authenticated Shell) so it can trigger on the
 * very first anonymous visit — not gated behind a login.
 *
 * Display rules:
 *   1. Only when `beforeinstallprompt` actually fired (browser thinks
 *      this site is installable) — EXCEPT on iOS Safari, which never
 *      fires that event at all; there we show manual "Add to Home
 *      Screen" instructions instead (no programmatic install API
 *      exists there).
 *   2. Don't pester an already-installed PWA (display-mode standalone
 *      / iOS `navigator.standalone`).
 *   3. Show ONCE per browser, then either install or dismiss
 *      permanently via localStorage flags.
 *   4. Two triggers, either one qualifies:
 *      a. Fresh login (the `aurem_just_logged_in` flag set by
 *         Login / Signup / OAuthFinish) — original behavior.
 *      b. First-ever visit to the site at all (tracked via
 *         `aurem_pwa_first_visit_seen`, set the first time this
 *         component mounts in ANY browser, logged in or not).
 *   5. A short delay (6s) before surfacing, so it never competes with
 *      first paint / the page the visitor actually came to see.
 */
import React, { useEffect, useRef, useState } from "react";
import { X, Download, Smartphone, Share2, PlusSquare } from "lucide-react";
import { loadConsent } from "../lib/cookieConsentStorage";
import { Z_PWA_INSTALL_PROMPT } from "../lib/zIndex";

const DISMISSED_KEY   = "aurem_pwa_dismissed";
const INSTALLED_KEY   = "aurem_pwa_installed";
const FIRST_VISIT_KEY = "aurem_pwa_first_visit_seen";
const SHOW_DELAY_MS   = 6000;
const CONSENT_POLL_MS = 800;

function isIOS() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
}
function isStandalone() {
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    window.navigator.standalone === true // iOS Safari's own flag
  );
}
// 2026-09-21 — CookieConsentBanner occupies this EXACT screen region
// (fixed, bottom-right on desktop / full-width bottom bar on mobile —
// see its own file) for any logged-out first-time visitor, which is
// now ALSO exactly who this prompt targets. Reproduced live: the
// consent banner's "Accept all" button painted directly on top of
// this prompt's "not now" button, silently swallowing every click.
// Rather than another one-off z-index/position patch (this codebase
// already hit this exact class of bug once for the ORA GUIDE tooltip
// — see Z_FLOATING_GUIDE's history in lib/zIndex.js — and it recurred
// even after that fix), remove the collision at its source: never
// reveal this prompt while a consent decision is still outstanding.
function cookieConsentPending() {
  try {
    if (localStorage.getItem("aurem_token")) return false; // banner hides for logged-in users too
    return !loadConsent();
  } catch {
    return false;
  }
}
/** Waits for BOTH the courtesy delay and a resolved cookie-consent
 * decision (whichever finishes last) before calling `cb`. Returns a
 * cancel function for effect cleanup. */
function waitForOpenSlot(cb) {
  const deadline = Date.now() + SHOW_DELAY_MS;
  let timer = null;
  const tick = () => {
    if (Date.now() >= deadline && !cookieConsentPending()) {
      cb();
      return;
    }
    timer = setTimeout(tick, CONSENT_POLL_MS);
  };
  timer = setTimeout(tick, SHOW_DELAY_MS);
  return () => clearTimeout(timer);
}

// Computed ONCE at module load (not inside the component's effect) so
// React.StrictMode's dev-mode double-invoke of effects — mount →
// cleanup → mount again — can't have the second invocation see a
// flag the first invocation already wrote to localStorage a moment
// earlier and incorrectly bail out as "not the first visit anymore".
const _wasFirstVisit = typeof window !== "undefined" && !localStorage.getItem(FIRST_VISIT_KEY);
if (_wasFirstVisit) {
  try { localStorage.setItem(FIRST_VISIT_KEY, "1"); } catch {}
}

export default function PWAInstallPrompt() {
  const [visible, setVisible]     = useState(false);
  const [busy, setBusy]           = useState(false);
  const [iosMode, setIosMode]     = useState(false);
  const deferredEvent = useRef(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // Already installed → nothing to do.
    if (isStandalone()) {
      try { localStorage.setItem(INSTALLED_KEY, "1"); } catch {}
      return;
    }
    if (localStorage.getItem(INSTALLED_KEY) === "1") return;
    if (localStorage.getItem(DISMISSED_KEY) === "1") return;

    const freshLogin  = localStorage.getItem("aurem_just_logged_in") === "1";
    // Use the module-load-time snapshot (see `_wasFirstVisit` above),
    // not a fresh localStorage read here — see comment there for why.
    const firstVisit  = _wasFirstVisit;
    if (freshLogin) {
      try { localStorage.removeItem("aurem_just_logged_in"); } catch {}
    }
    const shouldTrigger = freshLogin || firstVisit;
    if (!shouldTrigger) return;

    // iOS Safari never fires `beforeinstallprompt` — no programmatic
    // install exists there. Show manual instructions instead, once
    // the courtesy delay has passed AND cookie consent is resolved.
    if (isIOS()) {
      return waitForOpenSlot(() => {
        setIosMode(true);
        setVisible(true);
      });
    }

    let cancelWait = null;
    function onPrompt(e) {
      e.preventDefault();
      deferredEvent.current = e;
      cancelWait = waitForOpenSlot(() => setVisible(true));
    }
    function onInstalled() {
      try { localStorage.setItem(INSTALLED_KEY, "1"); } catch {}
      setVisible(false);
    }
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
      if (cancelWait) cancelWait();
    };
  }, []);

  async function install() {
    const evt = deferredEvent.current;
    if (!evt) return;
    setBusy(true);
    try {
      await evt.prompt();
      const choice = await evt.userChoice;
      if (choice?.outcome === "accepted") {
        try { localStorage.setItem(INSTALLED_KEY, "1"); } catch {}
      } else {
        // Soft dismiss only — user said "not now", let `appinstalled`
        // / a future login retry.
      }
    } finally {
      setBusy(false);
      setVisible(false);
      deferredEvent.current = null;
    }
  }

  function dismiss() {
    try { localStorage.setItem(DISMISSED_KEY, "1"); } catch {}
    setVisible(false);
  }

  if (!visible) return null;

  return (
    <div
      data-testid="pwa-install-prompt"
      role="dialog"
      aria-label="Install AUREM"
      style={{
        position: "fixed",
        right: 18,
        bottom: 18,
        zIndex: Z_PWA_INSTALL_PROMPT,
        maxWidth: 360,
        padding: "16px 18px 14px",
        borderRadius: 6,
        background: "linear-gradient(180deg, #131a2c 0%, #0d1322 100%)",
        border: "1px solid rgba(255, 206, 122, 0.28)",
        boxShadow: "0 24px 60px rgba(0,0,0,0.55)",
        color: "#e9edf2",
        fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
        animation: "auremPwaIn 220ms ease-out",
      }}
    >
      <button
        onClick={dismiss}
        aria-label="Dismiss install prompt"
        data-testid="pwa-install-dismiss"
        style={{
          position: "absolute",
          top: 8, right: 8,
          width: 24, height: 24,
          background: "transparent",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 4,
          color: "rgba(255,255,255,0.5)",
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <X size={12} />
      </button>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <div
          style={{
            width: 38, height: 38, borderRadius: 8,
            background: "rgba(255, 206, 122, 0.12)",
            border: "1px solid rgba(255, 206, 122, 0.32)",
            display: "inline-flex", alignItems: "center",
            justifyContent: "center", flexShrink: 0,
            color: "#ffce7a",
          }}
        >
          <Smartphone size={18} />
        </div>
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 11, letterSpacing: "0.14em",
              color: "#ffce7a", fontFamily: "'JetBrains Mono', monospace",
              marginBottom: 4,
            }}
          >
            PWA · install AUREM
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.35 }}>
            Open AUREM straight from your home screen.
          </div>
          {iosMode ? (
            <div
              data-testid="pwa-install-ios-instructions"
              style={{
                fontSize: 12, color: "rgba(233,237,242,0.7)",
                marginTop: 6, lineHeight: 1.5,
                display: "flex", flexDirection: "column", gap: 4,
              }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Share2 size={13} style={{ flexShrink: 0, color: "#ffce7a" }} />
                1. Tap the <b>Share</b> icon in Safari's toolbar
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <PlusSquare size={13} style={{ flexShrink: 0, color: "#ffce7a" }} />
                2. Scroll down and tap <b>Add to Home Screen</b>
              </span>
            </div>
          ) : (
            <div
              style={{
                fontSize: 12, color: "rgba(233,237,242,0.7)",
                marginTop: 4, lineHeight: 1.4,
              }}
            >
              One tap to chat with ORA · works offline shell · faster than
              the browser tab.
            </div>
          )}
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        {iosMode ? (
          <button
            onClick={dismiss}
            data-testid="pwa-install-later"
            style={{
              flex: 1,
              padding: "9px 12px",
              borderRadius: 4,
              background: "#ffce7a",
              color: "#0a0e1a",
              border: "none",
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: "0.06em",
              fontFamily: "'JetBrains Mono', monospace",
              textTransform: "uppercase",
              cursor: "pointer",
            }}
          >
            got it
          </button>
        ) : (
          <>
            <button
              onClick={install}
              disabled={busy}
              data-testid="pwa-install-confirm"
              style={{
                flex: 1,
                padding: "9px 12px",
                borderRadius: 4,
                background: "#ffce7a",
                color: "#0a0e1a",
                border: "none",
                fontSize: 12,
                fontWeight: 700,
                letterSpacing: "0.06em",
                fontFamily: "'JetBrains Mono', monospace",
                textTransform: "uppercase",
                cursor: busy ? "wait" : "pointer",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
              }}
            >
              <Download size={12} /> {busy ? "installing…" : "install"}
            </button>
            <button
              onClick={dismiss}
              data-testid="pwa-install-later"
              style={{
                padding: "9px 12px",
                borderRadius: 4,
                background: "transparent",
                color: "rgba(233,237,242,0.7)",
                border: "1px solid rgba(255,255,255,0.12)",
                fontSize: 12,
                letterSpacing: "0.04em",
                fontFamily: "'JetBrains Mono', monospace",
                cursor: "pointer",
              }}
            >
              not now
            </button>
          </>
        )}
      </div>
      <style>{`@keyframes auremPwaIn {
        from { opacity: 0; transform: translateY(12px); }
        to   { opacity: 1; transform: translateY(0); }
      }`}</style>
    </div>
  );
}
