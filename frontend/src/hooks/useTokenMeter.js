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

export function useTokenMeter() {
  const [meter, setMeter] = useState(null);
  const [loading, setLoading] = useState(true);
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;
    async function load() {
      try {
        const r = await api.get("/usage/me/meter");
        if (activeRef.current) setMeter(r.data);
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
