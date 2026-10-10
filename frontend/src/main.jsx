import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { mountApp } from "./lib/mountApp";
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
if (typeof window !== "undefined" && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .catch(() => { /* HTTP/non-supported — ignore silently */ });
  });
}

mountApp(
  ReactDOM.createRoot,
  document.getElementById("root"),
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// 2026-10-06 — mount watchdog counterpart. `main.jsx` reaching this
// line means the entry bundle actually loaded and executed (it never
// runs at all on a 404/parse-error script tag — the stale-index.html-
// vs-rotated-asset-hash failure mode a code review flagged after a
// founder report of the homepage staying stuck on the static SEO
// snapshot baked into `#root`). The watchdog in index.html reloads
// once if this flag never gets set.
window.__auremAppMounted = true;
