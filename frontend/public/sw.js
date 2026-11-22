/**
 * sw.js — AUREM Service Worker
 *
 * Strategy:
 *   • App shell (HTML + favicons + manifest + webp bg)    → precache
 *   • Static assets (JS/CSS chunks)                       → stale-while-revalidate
 *   • Backend API (/api/aurem-dev/*)                      → network-only (never stale)
 *   • Everything else                                     → network with cache fallback
 *
 * Versioning: bump CACHE_VERSION on shipped changes so old caches purge.
 *
 * 2026-11-11 — Fix 4 root cause: a founder-reported "ghost" link row
 * on /harness ("Watch the 3-minute demo" etc., seen once, never
 * again) does not exist anywhere in the current HarnessPage.jsx or in
 * harness-page-content.json — it was a ONE-TIME stale-while-revalidate
 * hit: the JS bundle's cache-first-then-update strategy below serves
 * whatever bundle is already cached on the FIRST request after a
 * deploy, while fetching the new one in the background for next time.
 * A visitor who had an old (pre-harness-redesign) bundle cached from
 * before this feature existed would see that old bundle once, then
 * get the current one on every request after. Bumping the version
 * here purges every visitor's stale cache on next activate.
 *
 * 2026-11-12 — same root cause bit again, this time site-wide: a
 * production deploy RCA (deployer-agent) confirmed the correct Fix
 * 7/8 code (harness auth branding, ?next= passthrough, Home-link fix)
 * was built and live on the server, but returning browsers still
 * showed the OLD "ORA by Aurem" branding because CACHE_VERSION here
 * was never bumped for that deploy — stale-while-revalidate kept
 * serving the cached pre-Fix-7/8 JS bundle. Bumping again. The
 * durable fix (main.jsx) now also reloads the page ONCE when a new
 * service worker takes control, so this class of bug can't recur
 * silently even if a future change forgets to bump this constant.
 */
// 2026-11-22 - v6. The v5 worker served JS/CSS stale-while-
// revalidate: after a deploy, visitors kept getting the previous
// deploy's hashed bundle from cache (the watchdog's one reload could
// not fix it, because the worker re-served the stale bundle). JS/CSS
// are now network-first (cache only as an offline fallback), and no
// asset response whose body is HTML is ever cached - a host SPA
// fallback answering a missing asset with index.html must not poison
// the cache under a .js/.css URL.
const CACHE_VERSION = "aurem-v6";
const STATIC_CACHE  = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

const APP_SHELL = [
  "/",
  "/site.webmanifest",
  "/favicon.ico",
  "/favicon-32.png",
  "/favicon-192.png",
  "/favicon-512.png",
  "/apple-touch-icon.png",
  "/aurem-bg.webp",
  "/aurem-bg-mobile.webp",
  "/og-image.jpg",
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(STATIC_CACHE).then((c) =>
      c.addAll(APP_SHELL).catch(() => null)
    )
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !k.startsWith(CACHE_VERSION))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Never cache API calls — chat/SSE/auth must hit the live backend.
  if (url.pathname.startsWith("/api/")) return;

  // Never intercept cross-origin requests we don't control.
  if (url.origin !== self.location.origin) return;

  // SSE / event-stream is incompatible with cache.
  if (req.headers.get("accept")?.includes("text/event-stream")) return;

  // Navigation requests → network-first with offline shell fallback.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => null);
          return res;
        })
        .catch(() =>
          caches.match(req).then((m) => m || caches.match("/"))
        )
    );
    return;
  }

  // 2026-11-22 - JS/CSS are network-first. Content-hashed chunks
// (index-<hash>.js) change on every deploy, and the stale-while-
// revalidate branch below used to hand the previous deploy's bundle
// to the page while the network refreshed in the background. When
// the old hash no longer exists on the server, the app mounts
// against a stale bundle - or against an HTML SPA fallback - and the
// homepage stays stuck on its SEO snapshot. Fetch the network first;
// fall back to the cache only when the network is unreachable.
// Never cache a response whose body is HTML.
if (url.pathname.endsWith(".js") || url.pathname.endsWith(".css")) {
  event.respondWith(
    fetch(req)
      .then((res) => {
        const type = res.headers.get("content-type") || "";
        if (res && res.status === 200 && !type.includes("text/html")) {
          const copy = res.clone();
          caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
  return;
}

// Static assets (images/fonts) - stale-while-revalidate.
// (JS/CSS are handled by the network-first branch above, 2026-11-22:
// a stale chunk is worse than a slow one - see the CACHE_VERSION note.)
  if (
    url.pathname.endsWith(".woff2") ||
    url.pathname.endsWith(".webp") ||
    url.pathname.endsWith(".png")  ||
    url.pathname.endsWith(".jpg")  ||
    url.pathname.endsWith(".svg")  ||
    url.pathname.endsWith(".ico")  ||
    url.pathname.endsWith(".webmanifest")
  ) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req).then((res) => {
          const type = res.headers.get("content-type") || "";
          if (res && res.status === 200 && !type.includes("text/html")) {
            const copy = res.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, copy)).catch(() => null);
          }
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // Default → network with cache fallback.
  event.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});

// Allow the app to ask the SW to skip waiting (e.g. after a deploy banner).
self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});
