import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import RouteErrorBoundary from "./components/RouteErrorBoundary";
import "./index.css";
// Iter 388-p1 — Frontend Sentry wiring (Item #20).  NO-OP unless
// REACT_APP_SENTRY_DSN is set in frontend/.env — safe to import
// unconditionally.  See src/lib/sentry.js for activation steps.
import { initSentry } from "./lib/sentry";
initSentry();
// Iter 212h — silent global error reporter; POSTs every console.error
// and unhandledrejection to /admin/errors/report so prod bugs surface
// in the Admin → Errors tab instead of disappearing into the void.
import "./utils/errorReporter";

// PWA — register the service worker (sw.js) once the page is settled.
// Stays no-op in dev (Vite serves over HTTP, browsers refuse SW on http://).
//
// 2026-11-12 — durable fix for a production RCA: sw.js already calls
// self.skipWaiting() on install and self.clients.claim() on activate,
// so a new SW version takes control of already-open tabs immediately
// — but nothing told the PAGE to actually use the new JS it just
// activated for. A returning visitor kept running the OLD bundle
// (old branding/copy/behavior) in memory until their next full
// navigation, even though the new SW + cache were already live.
// `controllerchange` fires once per new SW taking control — including
// on a visitor's very FIRST-EVER install (no previous controller),
// where a reload would be pointless (nothing stale to replace). Only
// attach the reload-once listener when this page was ALREADY
// controlled by a prior SW, i.e. this is a genuine update, not a
// first visit.
if (typeof window !== "undefined" && "serviceWorker" in navigator) {
  const hadControllerAlready = !!navigator.serviceWorker.controller;
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then((registration) => {
        // Iter 2026-11-20 — UI states wiring (Phase 2): when a new
        // service worker is found and reaches "installed" while this
        // page is already controlled (a genuine update, not a first
        // install), tell the app so it can show the library
        // UpdateRequired banner. The update itself still applies
        // through the existing controllerchange reload below.
        if (!registration) return;
        registration.addEventListener("updatefound", () => {
          const worker = registration.installing;
          if (!worker) return;
          worker.addEventListener("statechange", () => {
            if (worker.state === "installed" && navigator.serviceWorker.controller) {
              window.dispatchEvent(new CustomEvent("ora:update-available", { detail: { registration } }));
            }
          });
        });
      })
      .catch(() => { /* HTTP/non-supported — ignore silently */ });
  });
  if (hadControllerAlready) {
    let swRefreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (swRefreshing) return;
      swRefreshing = true;
      window.location.reload();
    });
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <RouteErrorBoundary>
      <App />
    </RouteErrorBoundary>
  </React.StrictMode>
);

// 2026-10-06 — mount watchdog counterpart. `main.jsx` reaching this
// line means the entry bundle actually loaded and executed (it never
// runs at all on a 404/parse-error script tag — the stale-index.html-
// vs-rotated-asset-hash failure mode a code review flagged after a
// founder report of the homepage staying stuck on the static SEO
// snapshot baked into `#root`). The watchdog in index.html reloads
// once if this flag never gets set.
window.__auremAppMounted = true;
