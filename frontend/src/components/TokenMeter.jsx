/**
 * TokenMeter.jsx — Master Prompt v2 PART 4.3, THE ONE token meter.
 *
 * Replaces TokenBell.jsx + TokenBanner.jsx (both deleted this turn —
 * see their removal in the same commit). Strict display rules:
 *   - Shows ONLY the remaining balance + a bar + the refill date.
 *   - NEVER a per-action/per-tool-call number anywhere.
 *   - Hidden entirely until GET /usage/me/meter says `visible: true`
 *     (server-side: paid plan + TOKEN_BILLING_MODE != "off" — see
 *     services/wallet_display.py). While billing mode stays "off"
 *     for this delivery, this means the meter renders for NOBODY in
 *     production — same "nothing changes for real users yet" rule
 *     every other Master Prompt v2 part follows.
 *   - 20%/5%/0% thresholds each fire their one-time banner/modal
 *     exactly once per session (localStorage, same pattern
 *     TokenBell.jsx used) — crossing back above a threshold and back
 *     down re-fires it (consistent with the old bell's reset rule).
 *   - The 0% "stop" state shows an informational modal with a
 *     Recharge CTA. It does NOT globally block every action button
 *     (rollback/export/results/PR view/billing/login must stay
 *     usable per spec) — the REAL enforcement of "no new chargeable
 *     action at 0 balance" already happens server-side (services/
 *     wallet.py::assert_hold_entitled's mode="on" 402), tested in
 *     earlier Master Prompt v2 parts. This modal is a UX nicety on
 *     top of that existing gate, not a duplicate client-side gate.
 */
import React, { useEffect, useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

const SEEN_KEY = "aurem_meter_threshold_seen";

function loadSeen() {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
  } catch {
    return [];
  }
}
function saveSeen(arr) {
  localStorage.setItem(SEEN_KEY, JSON.stringify(arr));
}

function toneFor(threshold) {
  if (threshold === "stop") return { bg: "rgba(255,77,77,0.10)", border: "rgba(255,77,77,0.45)", color: "#ff8585" };
  if (threshold === "red") return { bg: "rgba(255,77,77,0.08)", border: "rgba(255,77,77,0.3)", color: "#ff8585" };
  if (threshold === "yellow") return { bg: "rgba(255,196,0,0.10)", border: "rgba(255,196,0,0.45)", color: "#ffcf5c" };
  return { bg: "transparent", border: "var(--border)", color: "var(--text-dim)" };
}

export default function TokenMeter({ collapsed }) {
  const [meter, setMeter] = useState(null);
  const [showStopModal, setShowStopModal] = useState(false);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const seenRef = useRef(loadSeen());
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const r = await api.get("/usage/me/meter");
        if (active) setMeter(r.data);
      } catch {
        /* ignore — meter just stays hidden */
      }
    }
    load();
    const id = setInterval(load, 60000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!meter || !meter.visible) return;
    const threshold = meter.threshold;
    if (threshold === "ok" || !threshold) return;
    if (seenRef.current.includes(threshold)) return;
    seenRef.current = Array.from(new Set([...seenRef.current, threshold]));
    saveSeen(seenRef.current);
    setBannerDismissed(false);
    if (threshold === "stop") setShowStopModal(true);
  }, [meter]);

  if (!meter || !meter.visible) return null;

  const { balance, base, pct_remaining, refill_date, threshold } = meter;
  const tone = toneFor(threshold);
  const barPct = pct_remaining == null ? 100 : Math.max(0, Math.min(100, pct_remaining));
  const refillLabel = refill_date
    ? new Date(refill_date * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : null;

  return (
    <>
      <div
        data-testid="token-meter"
        onClick={() => navigate("/settings#billing")}
        title={refillLabel ? `Refills ${refillLabel}` : undefined}
        style={{
          display: "flex", flexDirection: collapsed ? "row" : "column",
          alignItems: collapsed ? "center" : "stretch",
          gap: 4, padding: collapsed ? 0 : "6px 10px",
          cursor: "pointer", border: `1px solid ${tone.border}`,
          borderRadius: 4, background: tone.bg,
        }}
      >
        {!collapsed && (
          <span data-testid="token-meter-balance" style={{ fontSize: 11, fontWeight: 600, color: tone.color }}>
            {balance.toLocaleString()} tokens left
          </span>
        )}
        <div
          data-testid="token-meter-bar"
          style={{
            width: collapsed ? 20 : "100%", height: 4, borderRadius: 2,
            background: "rgba(255,255,255,0.1)", overflow: "hidden",
          }}
        >
          <div style={{ width: `${barPct}%`, height: "100%", background: tone.color || "var(--ok)" }} />
        </div>
        {!collapsed && refillLabel && (
          <span data-testid="token-meter-refill" style={{ fontSize: 10, color: "var(--text-dim)" }}>
            Refills {refillLabel}
          </span>
        )}
      </div>

      {(threshold === "yellow" || threshold === "red") && !bannerDismissed && (
        <div data-testid="token-meter-banner" data-tone={threshold} style={{
          display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
          background: tone.bg, border: `1px solid ${tone.border}`, borderRadius: 6,
          fontSize: 12, color: tone.color,
        }}>
          <span style={{ flex: 1 }}>
            {threshold === "red" ? "Running low on tokens." : "Getting close to your limit."}
          </span>
          <a href="/settings#billing" data-testid="token-meter-banner-upgrade">Recharge →</a>
          <button
            type="button" data-testid="token-meter-banner-dismiss"
            onClick={() => setBannerDismissed(true)}
            style={{ background: "transparent", border: "none", color: tone.color, cursor: "pointer" }}
          >
            ×
          </button>
        </div>
      )}

      {showStopModal && (
        <div
          data-testid="token-meter-stop-modal-overlay"
          onClick={() => setShowStopModal(false)}
          style={{
            position: "fixed", inset: 0, zIndex: 9500,
            background: "rgba(0,0,0,0.7)", display: "flex",
            alignItems: "center", justifyContent: "center", padding: 24,
          }}
        >
          <div
            data-testid="token-meter-stop-modal"
            role="dialog" aria-modal="true"
            onClick={(e) => e.stopPropagation()}
            style={{
              maxWidth: 420, background: "var(--panel)", border: "1px solid var(--danger)",
              borderRadius: 6, padding: 24, color: "var(--text)", textAlign: "center",
            }}
          >
            <h2 style={{ margin: "0 0 10px", fontSize: 20 }}>Tokens finished</h2>
            <p style={{ color: "var(--text-dim)", fontSize: 13, margin: "0 0 20px" }}>
              Recharge to keep building. Your results, rollback, export, and billing stay available either way.
            </p>
            <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
              <button
                type="button" data-testid="token-meter-stop-modal-dismiss"
                className="btn-ghost" onClick={() => setShowStopModal(false)}
              >
                Close
              </button>
              <button
                type="button" data-testid="token-meter-stop-modal-cta"
                className="btn-primary"
                onClick={() => { setShowStopModal(false); navigate("/settings#billing"); }}
              >
                Recharge
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
