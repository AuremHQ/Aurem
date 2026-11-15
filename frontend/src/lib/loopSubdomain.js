/**
 * loopSubdomain.js — 2026-11-15, Nov 10 founder decision: the harness
 * gets its own home at loop.auremcto.com (separate homepage; ORA
 * homepage links to it; /harness redirects there once DNS is live).
 *
 * This is the ONE place that knows the subdomain hostname, so the
 * routing in App.jsx and the JSON-LD URLs in HarnessPage.jsx can
 * never drift apart. Pure string check — no network, no storage.
 * On any other host (auremcto.com, localhost, Emergent preview) it
 * returns false and the app behaves exactly as today.
 */

export const LOOP_SUBDOMAIN_HOST = "loop.auremcto.com";

export function isLoopSubdomain(hostname) {
  const host =
    typeof hostname === "string"
      ? hostname
      : (typeof window !== "undefined" && window.location?.hostname) || "";
  return host.trim().toLowerCase() === LOOP_SUBDOMAIN_HOST;
}
