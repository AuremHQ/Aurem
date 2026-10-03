/**
 * MaintenanceGate.jsx — 2026-08.
 *
 * Wraps the whole app. Polls the public, unauthenticated
 * /maintenance/status endpoint:
 *   - manual_enabled=true  → show the maintenance screen immediately
 *     (admin/founder still bypass, so they can verify a fresh deploy).
 *   - fetch fails 2x in a row (~6s grace) → treat as a real outage
 *     and show the "brief hiccup, retrying" screen instead of a blank
 *     page / raw network error.
 * Keeps polling while blocked so it self-clears the moment the
 * backend/manual-flag recovers — no reload needed.
 *
 * The app stays MOUNTED underneath the screen (it only covers it), so a
 * half-typed message, scroll position and open panels are all still
 * there the instant the backend is back — the user lands exactly where
 * they were. While blocked we poll fast (and on tab-focus / back-online)
 * so recovery shows within ~half a second instead of up to 3s.
 */
import React, { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { getUser, isAdminOrFounder } from "../lib/api";
import MaintenanceScreen from "./MaintenanceScreen";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
const POLL_MS = 3000;
const BLOCKED_POLL_MS = 500;
const BLOCKED_TIMEOUT_MS = 2000;
const FAILS_BEFORE_BLOCK = 2;

// Routes an admin MUST be able to reach even during manual maintenance
// mode — otherwise a fresh/logged-out admin session could never sign
// in to turn maintenance off. These paths don't represent the "app"
// experience a maintenance screen protects.
const EXEMPT_PREFIXES = ["/login", "/signup", "/admin", "/magic-login", "/oauth-finish", "/reset-password", "/verify"];

export default function MaintenanceGate({ children }) {
  const location = useLocation();
  const pathExempt = EXEMPT_PREFIXES.some((p) => location.pathname.startsWith(p));
  const bypass = pathExempt || isAdminOrFounder(getUser());
  const [state, setState] = useState({ blocked: false, manual: false, message: "", window: "" });
  const failCountRef = useRef(0);
  const cancelledRef = useRef(false);
  const blockedRef = useRef(false);

  useEffect(() => {
    if (bypass || !BACKEND) return;
    cancelledRef.current = false;
    let timer = null;

    async function poll() {
      try {
        const res = await fetch(`${BACKEND}/api/aurem-dev/maintenance/status`, {
          cache: "no-store",
          signal: AbortSignal.timeout(blockedRef.current ? BLOCKED_TIMEOUT_MS : 5000),
        });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        if (cancelledRef.current) return;
        failCountRef.current = 0;
        blockedRef.current = !!data.manual_enabled;
        setState({
          blocked: !!data.manual_enabled,
          manual: !!data.manual_enabled,
          message: data.message || "",
          window: data.window || "",
        });
      } catch {
        if (cancelledRef.current) return;
        failCountRef.current += 1;
        if (failCountRef.current >= FAILS_BEFORE_BLOCK) {
          blockedRef.current = true;
          setState((s) => (s.manual ? s : { blocked: true, manual: false, message: "", window: "" }));
        }
      }
    }

    // Self-scheduling (not setInterval) so the delay can shrink the
    // moment we block, and a slow poll can never overlap the next one.
    async function loop() {
      await poll();
      if (cancelledRef.current) return;
      timer = setTimeout(loop, blockedRef.current ? BLOCKED_POLL_MS : POLL_MS);
    }
    function pollNow() {
      if (cancelledRef.current || !blockedRef.current) return;
      clearTimeout(timer);
      loop();
    }
    const onVisible = () => { if (document.visibilityState === "visible") pollNow(); };

    loop();
    window.addEventListener("online", pollNow);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelledRef.current = true;
      clearTimeout(timer);
      window.removeEventListener("online", pollNow);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [bypass]);

  return (
    <>
      {children}
      {!bypass && state.blocked && (
        <MaintenanceScreen manual={state.manual} message={state.message} window={state.window} />
      )}
    </>
  );
}
