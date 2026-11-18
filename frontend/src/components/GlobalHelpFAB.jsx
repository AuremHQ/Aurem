/**
 * GlobalHelpFAB.jsx — Floating "Need help?" button, mounted globally.
 *
 * Shows only when:
 *   · user is logged in (JWT in localStorage), AND
 *   · current route isn't the marketing/auth surface
 *     (/, /login, /signup, /verify, /support, /demo, /bug-hunt, /admin/*)
 *
 * Clicking opens the SupportPopup which posts to /support/tickets and
 * writes into the same cto_support collection the admin Support panel
 * reads.
 */
import React, { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { SupportButton } from "./SupportPopup";
// Paths where the FAB stays hidden — marketing, auth, admin (admin has
// its own inbox and doesn't need to file tickets to itself), and pages
// that already have their own support surface.
// 2026-08-20 — exported so `OraGuideMascot` (the new fixed-position
// help entry point that replaces this FAB in App.jsx) can reuse the
// exact same "where does help even make sense" rules without forking
// the list.
const HIDE_PREFIXES = [
  "/login", "/signup", "/verify", "/support",
  "/demo", "/bug-hunt", "/why-ora",
  "/admin",
  "/dev/",
  // 2026-11-11 — /harness is Aurem Loop's standalone marketing page
  // (a free, separate tool), not an in-app ORA surface — the mascot/
  // FAB's "file a support ticket" entry point doesn't apply there.
  "/harness",
];
const EXACT_HIDE = new Set(["/"]);

export function shouldHide(pathname) {
  if (EXACT_HIDE.has(pathname)) return true;
  return HIDE_PREFIXES.some((p) => pathname.startsWith(p));
}

