/**
 * hooks/useOverlaySlot.js — UI-fix D1 (2026-10)
 *
 * Coordinates the onboarding overlays so a new user never sees two
 * blocking ones at once: NewUserWizard (incl. the mandatory gate) >
 * StarterChoiceModal > the ORA GUIDE mascot's auto-open bubble. Each
 * overlay "claims" its slot while open via `useOverlaySlot`; the
 * mascot checks `useCanShowOverlay("mascot")` before auto-popping so
 * it never fights a higher-priority overlay for the screen. Plain
 * module-level pub/sub (same `window` event idiom this codebase
 * already uses elsewhere, just kept in-process since every consumer
 * here lives in the same React tree) — no new dependency.
 */
import { useEffect, useSyncExternalStore } from "react";

const PRIORITY = { wizard: 3, starter: 2, mascot: 1 };
const active = new Set();
const listeners = new Set();
function notify() { listeners.forEach((l) => l()); }
function subscribe(cb) { listeners.add(cb); return () => listeners.delete(cb); }

/** Announce whether this overlay is currently open. Call on every render
 * with the overlay's own `isOpen` boolean — registers/deregisters itself. */
export function useOverlaySlot(name, isOpen) {
  useEffect(() => {
    if (isOpen) active.add(name); else active.delete(name);
    notify();
    return () => { active.delete(name); notify(); };
  }, [name, isOpen]);
}

/** True if no HIGHER-priority overlay than `name` is currently open. */
export function useCanShowOverlay(name) {
  return useSyncExternalStore(subscribe, () => {
    const myPriority = PRIORITY[name];
    for (const activeName of active) {
      if (activeName !== name && PRIORITY[activeName] > myPriority) return false;
    }
    return true;
  });
}
