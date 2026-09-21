/**
 * zIndex.js — single source of truth for cross-component stacking.
 * 2026-08-24 — the floating ORA mascot was dead in production because
 * its hardcoded 9990 sat UNDER the cookie banner's hardcoded 10000.
 * Shared constants make that class of silent regression impossible:
 * change one layer here and every consumer moves together.
 */
export const Z_COOKIE_BANNER = 10000;
export const Z_FLOATING_GUIDE = 10001; // must stay above the banner
// 2026-09-09 — the engagement-nudge toggle-badge (EngagementNudge.jsx)
// floats in the OPPOSITE corner from ORA GUIDE (bottom-left vs
// bottom-right) so the two never visually stack, but still needs a
// tracked value here rather than a hardcoded number — same rationale
// as every other constant on this file.
export const Z_ENGAGEMENT_NUDGE = 10001;
// 2026-08-23 — the collapsed "ADVISOR" re-open tab (AskAdvisorReal.jsx)
// is `fixed`, vertically-centered, anchored to the right edge — the
// EXACT same screen region LiveTaskPopup.jsx's live ship/task progress
// panel occupies (fixed, right:16, top:50%, hardcoded zIndex:7500).
// A real user got their Advisor toggle completely covered/unclickable
// by the live-ship popup mid-task (right when a GitHub auth hiccup
// also hit), with no way to bring the sidebar back until the popup
// cleared. Must always render above ANY transient floating panel.
export const Z_ADVISOR_TOGGLE = 10002;
// 2026-09-06 — toasts (components/Toast.jsx) were hardcoded to 9999,
// BELOW every full-screen modal overlay in the app (most use 10000,
// e.g. VisibilityKitPanel). A conflict/error toast fired correctly
// into the DOM but rendered visually UNDER the still-open modal's
// backdrop — invisible to the user despite existing. Toasts are
// transient system feedback and must always be the topmost layer,
// above any modal that might still be open when they fire.
export const Z_TOAST = 10003;
// 2026-09-21 — the PWA install prompt (PWAInstallPrompt.jsx) is now
// mounted globally (also for logged-out first-time visitors, not
// just post-login), sharing the bottom-right corner with the cookie
// consent banner for that same audience. The real fix is temporal
// (never reveal the prompt while consent is still pending — see
// `waitForOpenSlot` in that file), but it still needs a tracked
// z-index like every other floating layer here rather than a
// hardcoded number, so a future consumer can reason about stacking
// order without re-deriving it from scratch.
export const Z_PWA_INSTALL_PROMPT = 9995;
