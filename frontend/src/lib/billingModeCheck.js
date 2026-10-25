/**
 * lib/billingModeCheck.js — ITEM 5(b)/5(d), independent-review fix
 * round.
 *
 * frontend VITE_TOKEN_BILLING_MODE (lib/billingCopy.js) is baked in
 * at BUILD time; backend TOKEN_BILLING_MODE (services.wallet.
 * billing_mode()) is read at RUNTIME. If the backend flips to
 * "shadow"/"on" without a matching frontend rebuild+redeploy, users
 * see legacy task wording while actually being billed in tokens.
 *
 * `checkBillingModeDriftOnce()` compares the two and, on mismatch,
 * logs a console warning and fires ONE analytics event
 * (trackBillingModeMismatch) — diagnostic only, no UI change. Runs
 * at most once per page load (module-level guard).
 *
 * Scope, by design: only wired into the AUTHENTICATED shell
 * (components/nav/RailShell.jsx) — a logged-in user's own screens
 * (Settings, UsageLimitModal, TokenMeter) are the places a stale
 * build vs. live-billing mismatch actually matters to THAT user, and
 * those screens MAY call fetchRuntimeBillingMode() directly to use
 * the live value instead of the build constant if they choose to.
 * Public PRERENDERED pages (Landing.jsx etc.) deliberately do NOT
 * call this: they're prerendered at build time for SEO, so the
 * build-time constant is definitionally correct for that snapshot —
 * checking a live value at runtime there would just be noise (every
 * visitor re-fetching to "detect" a mismatch the prerender can't act
 * on anyway) and would defeat the point of prerendering stable copy.
 */
import { api } from "./api";
import { trackBillingModeMismatch } from "./analytics";

let _hasRunThisPageLoad = false;

export async function fetchRuntimeBillingMode() {
  try {
    const { data } = await api.get("/payments/billing-mode");
    const mode = data && data.mode;
    return typeof mode === "string" ? mode : null;
  } catch (_) {
    return null;
  }
}

export async function checkBillingModeDriftOnce({ buildMode } = {}) {
  if (_hasRunThisPageLoad) return;
  _hasRunThisPageLoad = true;

  const runtimeMode = await fetchRuntimeBillingMode();
  if (!runtimeMode || !buildMode) return;
  if (runtimeMode === buildMode) return;

  try {
    // eslint-disable-next-line no-console
    console.warn(
      `[billing-mode] frontend build (${buildMode}) and backend runtime ` +
        `(${runtimeMode}) disagree — a stale deploy may be showing the ` +
        `wrong billing wording.`,
    );
  } catch (_) {
    // never let a console failure break the caller
  }
  trackBillingModeMismatch({ build_mode: buildMode, runtime_mode: runtimeMode });
}

// Test-only reset of the module-level once-guard.
export function _resetBillingModeDriftCheckForTests() {
  _hasRunThisPageLoad = false;
}
