/**
 * PaywallModal.jsx — Master Prompt v2 PART 4.2.
 *
 * Opens when a Fix is clicked and the server says not entitled
 * (402 `payment_required`/`tokens_exhausted` — see backend
 * services/fix_entitlement.py / services/wallet.py::
 * assert_hold_entitled). Shows the plans with ONE estimate line
 * ("$9 gets you about N fixes", from GET /payments/fix-estimate —
 * the rolling-cost-derived math, never a per-action dollar figure).
 * No refund line: no refund policy exists anywhere in this repo's
 * code/config (confirmed by grep) — listed as a missing decision in
 * the delivery report rather than invented here.
 *
 * Closeable — onClose just returns to the issues list; the fix stays
 * locked either way (this component never unlocks anything itself).
 *
 * After Stripe Checkout redirects back with `?session_id=...`, this
 * polls GET /payments/status/:session_id and calls onUnlocked() ONLY
 * when the server's OWN `fix_entitled` field is true. The redirect's
 * `?upgraded=1` flag (or any other URL param) is NEVER read as proof
 * of entitlement — a forged `?upgraded=1` with no real session_id,
 * or a stale/foreign session_id, simply polls forever/404s and never
 * unlocks (see routers/payments.py::payment_status, which already
 * scopes session_id to the caller and never writes fix_entitlement
 * from anything client-controlled).
 */
import React, { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

const POLL_MS = 2500;
const MAX_POLLS = 120; // 5 minutes

export default function PaywallModal({ open, onClose, onUnlocked, planPriceUsd = 9 }) {
  const [estimate, setEstimate] = useState(null);
  const [polling, setPolling] = useState(false);
  const pollCountRef = useRef(0);
  const timerRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    api.get("/payments/fix-estimate", { params: { price_usd: planPriceUsd } })
      .then((r) => setEstimate(r.data))
      .catch(() => setEstimate(null));
  }, [open, planPriceUsd]);

  useEffect(() => {
    if (!open) return;
    const params = new URLSearchParams(window.location.search);
    const sessionId = params.get("session_id");
    if (!sessionId) return;
    setPolling(true);
    const poll = async () => {
      pollCountRef.current += 1;
      try {
        const r = await api.get(`/payments/status/${sessionId}`);
        if (r.data?.fix_entitled === true) {
          setPolling(false);
          onUnlocked && onUnlocked();
          return;
        }
      } catch {
        // keep polling — a transient Stripe/network error must not
        // give up and silently leave the user stuck locked.
      }
      if (pollCountRef.current < MAX_POLLS) {
        timerRef.current = setTimeout(poll, POLL_MS);
      } else {
        setPolling(false);
      }
    };
    poll();
    return () => timerRef.current && clearTimeout(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const startCheckout = async (plan) => {
    const r = await api.post("/payments/checkout", { plan, origin_url: window.location.origin });
    if (r.data?.checkout_url) {
      window.location.href = r.data.checkout_url;
    }
  };

  if (!open) return null;

  return (
    <div
      data-testid="paywall-modal-overlay"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 9400, background: "rgba(0,0,0,0.7)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
    >
      <div
        data-testid="paywall-modal"
        role="dialog" aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: 480, width: "100%", background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 8, padding: 28,
        }}
      >
        <h2 style={{ marginTop: 0 }}>Unlock Fix</h2>

        {polling ? (
          <p data-testid="paywall-modal-polling">Confirming your payment…</p>
        ) : (
          <>
            {estimate && estimate.estimated_fixes != null && (
              <p data-testid="paywall-modal-estimate">
                ${estimate.price_usd} gets you about {Math.round(estimate.estimated_fixes)} fixes.
              </p>
            )}
            {/* No refund-policy line: none exists in code/config for
                this product — intentionally omitted rather than
                invented (see delivery report). */}
            <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
              <button type="button" data-testid="paywall-modal-close" className="btn-ghost" onClick={onClose}>
                Not now
              </button>
              <button
                type="button" data-testid="paywall-modal-checkout"
                className="btn-primary"
                onClick={() => startCheckout("starter")}
              >
                View plans
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
