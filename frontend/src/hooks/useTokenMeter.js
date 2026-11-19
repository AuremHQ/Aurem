/**
 * hooks/useTokenMeter.js — ITEM 8 (independent review).
 *
 * Extracted from TokenMeter.jsx's own inline fetch/poll logic so
 * Settings.jsx's balance card and TokenMeter.jsx read from the EXACT
 * SAME data source (GET /usage/me/meter, services/wallet_display.py)
 * instead of two different numbers — Settings.jsx previously read the
 * legacy, non-gating `/usage/me` "remaining" field (a raw internal
 * token estimate decoupled from real billing, per services/usage.py's
 * own docstring), while TokenMeter read the real wallet balance.
 *
 * Same display contract as before: `visible` is server-computed
 * (paid plan + TOKEN_BILLING_MODE != "off"), `balance`/`base`/
 * `pct_remaining`/`refill_date`/`threshold` only — never a per-action
 * number (services/wallet_display.py enforces this server-side; this
 * hook does not add fields).
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

const POLL_MS = 60000;

// Minor 13 - last-known meter: TOKENS USED paints the cached value
// at first paint instead of a dash; the fetch below still refreshes.
export const METER_CACHE_KEY = "aurem_token_meter_cache";

function readCachedMeter() {
  try {
    const raw = localStorage.getItem(METER_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || !("visible" in parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function useTokenMeter() {
  const [meter, setMeter] = useState(readCachedMeter);
  const [loading, setLoading] = useState(meter == null);
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;
    async function load() {
      try {
        const r = await api.get("/usage/me/meter");
        if (activeRef.current) setMeter(r.data);
        try { localStorage.setItem(METER_CACHE_KEY, JSON.stringify(r.data)); } catch { /* ignore */ }
      } catch (_) {
        // degrade silently — caller treats null/invisible the same as "hidden"
      } finally {
        if (activeRef.current) setLoading(false);
      }
    }
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      activeRef.current = false;
      clearInterval(id);
    };
  }, []);

  return { meter, loading };
}
