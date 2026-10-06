/**
 * cookieConsentStorage.js — localStorage consent persistence + tracker
 * gating helpers for CookieConsentBanner.jsx.
 *
 * Extracted from components/CookieConsentBanner.jsx (2026-08-27,
 * mechanical split — no behaviour change) to keep that file under the
 * platform's file-size guard.
 */
export const STORAGE_KEY = "aurem_consent";
export const VERSION = 1;

export const DEFAULT_CATS = {
  necessary:  true,   // always on, non-toggleable
  functional: true,
  analytics:  false,
  marketing:  false,
};

export function loadConsent() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.v !== VERSION) return null;
    return parsed;
  } catch (_) {
    return null;
  }
}

export function saveConsent(choice, cats) {
  const record = {
    v: VERSION,
    ts: new Date().toISOString(),
    choice,
    cats,
  };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
  } catch (_) { /* private mode etc. — silent */ }
  return record;
}

/**
 * Start / stop the browser trackers we ship in index.html.
 * index.html loads none of them until the visitor has opted in (see
 * `window.__auremHasConsent`), so a grant that arrives after page load
 * has to start the loaders itself — `window.__auremBootPixels` holds
 * them, each idempotent. A revoke can't unload a script that already
 * ran, but it stops FUTURE events and updates gtag consent mode.
 */
function bootTracker(key) {
  try { window.__auremBootPixels?.[key]?.(); } catch (_) { /* never break page */ }
}

function silenceTracker(name, bootedFlag) {
  const fn = window[name];
  if (typeof fn !== "function") return;
  if (window[bootedFlag]) {
    // Script already loaded and running: swap in a no-op so later
    // conversion events go nowhere.
    window[name] = function noop() {};
  } else if (Array.isArray(fn.queue)) {
    fn.queue = [];
  } else if (Array.isArray(fn.q)) {
    fn.q = [];
  }
}

export function applyConsentToTrackers(cats) {
  try {
    if (typeof window !== "undefined") {
      if (cats.marketing) {
        bootTracker("meta");
        bootTracker("openai");
      } else {
        silenceTracker("fbq", "_fbqBooted");
        silenceTracker("oaiq", "__oaiqBooted");
      }
      if (cats.analytics || cats.marketing) bootTracker("gtag");
    }
    if (typeof window !== "undefined" && typeof window.gtag === "function") {
      window.gtag("consent", "update", {
        ad_storage:         cats.marketing  ? "granted" : "denied",
        analytics_storage:  cats.analytics  ? "granted" : "denied",
        ad_user_data:       cats.marketing  ? "granted" : "denied",
        ad_personalization: cats.marketing  ? "granted" : "denied",
      });
    }
  } catch (_) { /* never break page for consent-plumbing */ }
}

export function isGpcOn() {
  try {
    return Boolean(navigator?.globalPrivacyControl);
  } catch (_) { return false; }
}
