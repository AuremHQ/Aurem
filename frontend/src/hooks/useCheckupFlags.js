/**
 * useCheckupFlags — shared, cached read of the checkup feature flags.
 * Fail-closed: any error means every flag is OFF, i.e. today's behaviour.
 */
import { useEffect, useState } from "react";
import { fetchCheckupFlags } from "../lib/checkupApi";

const TTL_MS = 60_000;
let cache = { at: 0, promise: null };

export function loadCheckupFlags() {
  const now = Date.now();
  if (cache.promise && now - cache.at < TTL_MS) return cache.promise;
  const p = fetchCheckupFlags().then((f) => {
    if (!f || !Object.keys(f).length) cache = { at: 0, promise: null }; // don't cache failures
    return f || {};
  });
  cache = { at: now, promise: p };
  return p;
}

export function resetCheckupFlagsCache() {
  cache = { at: 0, promise: null };
}

export default function useCheckupFlags() {
  const [flags, setFlags] = useState(null);
  useEffect(() => {
    let on = true;
    loadCheckupFlags().then((f) => on && setFlags(f));
    return () => { on = false; };
  }, []);
  return flags;
}

/** True only when `chat_clean_ui` is on for this user. */
export function useCleanChatUi() {
  const flags = useCheckupFlags();
  return !!flags?.chat_clean_ui;
}
