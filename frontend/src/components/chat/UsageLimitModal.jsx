/**
 * components/chat/UsageLimitModal.jsx — 2026-09-26.
 *
 * Product gap fix: Free is $0/forever, no card required, and there
 * was no hard-to-miss moment that told a user they'd hit their 10
 * free-tasks/month cap and could upgrade in one click. The existing
 * <TokenBanner> is a persistent thin strip above the composer — easy
 * to scroll past. This modal fires ONCE, the moment the server-side
 * `is_blocked` flag flips true, with a direct one-click upgrade to
 * Starter or Pro (same /payments/checkout call PricingCards uses —
 * no extra trip through /pricing required).
 *
 * Dismiss is remembered for this browser session only (sessionStorage)
 * so it doesn't nag on every reload once seen, but reappears next
 * session if the user is still blocked.
 */
import React, { useState } from "react";
import { X, Lock } from "lucide-react";
import useModalA11y from "../../hooks/useModalA11y";
import { api } from "../../lib/api";
import { toast } from "../Toast";

const DISMISS_KEY = "aurem_usage_limit_modal_dismissed";

const PLANS = [
  { id: "starter", label: "Starter", price: "$9/mo", tasks: "50 tasks/month" },
  { id: "pro",     label: "Pro",     price: "$19/mo", tasks: "300 tasks/month", popular: true },
];

export default function UsageLimitModal({ open, onClose, usage }) {
  const [busy, setBusy] = useState(null);
  const modalRef = React.useRef(null);
  useModalA11y({ ref: modalRef, isOpen: open, onClose });

  if (!open) return null;

  const taskCap = usage?.monthly_task_cap;
  const tasksThisMonth = usage?.tasks_this_month || 0;

  async function upgrade(tierId) {
    setBusy(tierId);
    try {
      const r = await api.post("/payments/checkout", {
        plan: tierId,
        origin_url: window.location.origin,
      });
      const url = r.data?.checkout_url || r.data?.url;
      if (url) {
        window.location.href = url;
      } else {
        toast({ message: "Could not start checkout — please retry.", kind: "error" });
        setBusy(null);
      }
    } catch {
      toast({ message: "Could not start checkout — please retry.", kind: "error" });
      setBusy(null);
    }
  }

  function dismiss() {
    try { sessionStorage.setItem(DISMISS_KEY, "1"); } catch { /* ignore */ }
    onClose?.();
  }

  return (
    <div
      data-testid="usage-limit-modal"
      ref={modalRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="usage-limit-modal-title"
      tabIndex={-1}
      onClick={(e) => { if (e.target === e.currentTarget) dismiss(); }}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0, 0, 0, 0.75)",
        backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 10_001,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(520px, 92vw)",
          background: "linear-gradient(135deg, #14100a 0%, #1a140c 100%)",
          border: "1px solid rgba(255,196,0,0.35)",
          borderRadius: 16,
          padding: 26,
          display: "flex", flexDirection: "column", gap: 18,
          boxShadow: "0 30px 70px -14px rgba(255,196,0,0.25), 0 0 0 1px rgba(255,196,0,0.08)",
        }}
      >
        <button
          type="button"
          data-testid="usage-limit-modal-close"
          onClick={dismiss}
          aria-label="Close"
          style={{
            position: "absolute", top: 16, right: 16,
            background: "none", border: "none", color: "var(--text-faint)",
            cursor: "pointer", padding: 4,
          }}
        >
          <X size={16} />
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: 36, height: 36, borderRadius: 8,
            background: "rgba(255,196,0,0.12)", border: "1px solid rgba(255,196,0,0.35)",
          }}>
            <Lock size={17} color="#ffcf5c" strokeWidth={2.2} />
          </div>
          <strong
            id="usage-limit-modal-title"
            className="serif"
            style={{ fontSize: 20, color: "#fff" }}
          >
            You've used all {taskCap ?? 10} free tasks this month
          </strong>
        </div>

        <p style={{ fontSize: 13, color: "var(--text-dim)", lineHeight: 1.6, margin: 0 }}>
          {tasksThisMonth}/{taskCap ?? 10} tasks used on the Free plan. Upgrade to keep
          ORA shipping — pick a plan below and you're back to work in one click.
        </p>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {PLANS.map((p) => (
            <button
              key={p.id}
              type="button"
              data-testid={`usage-limit-upgrade-${p.id}`}
              onClick={() => upgrade(p.id)}
              disabled={busy !== null}
              style={{
                display: "flex", flexDirection: "column", gap: 4,
                padding: "14px 14px",
                borderRadius: 10,
                textAlign: "left",
                cursor: busy !== null ? "default" : "pointer",
                border: p.popular ? "1px solid rgba(255,196,0,0.6)" : "1px solid var(--border)",
                background: p.popular ? "rgba(255,196,0,0.12)" : "rgba(255,255,255,0.03)",
                opacity: busy && busy !== p.id ? 0.5 : 1,
              }}
            >
              {p.popular && (
                <span style={{ fontSize: 9, letterSpacing: "0.08em", color: "#ffcf5c", fontWeight: 700 }}>
                  MOST POPULAR
                </span>
              )}
              <span style={{ fontSize: 15, fontWeight: 700, color: "#fff" }}>{p.label}</span>
              <span style={{ fontSize: 13, color: "var(--text-dim)" }}>{p.price}</span>
              <span style={{ fontSize: 11, color: "var(--text-faint)" }}>{p.tasks}</span>
              <span style={{
                marginTop: 6, fontSize: 11, fontWeight: 700, color: p.popular ? "#0a0a0e" : "#ffcf5c",
                background: p.popular ? "#ffcf5c" : "transparent",
                border: p.popular ? "none" : "1px solid rgba(255,196,0,0.4)",
                borderRadius: 6, padding: "6px 8px", textAlign: "center",
              }}>
                {busy === p.id ? "Opening checkout…" : `Upgrade to ${p.label} →`}
              </span>
            </button>
          ))}
        </div>

        <button
          type="button"
          data-testid="usage-limit-modal-later"
          onClick={dismiss}
          style={{
            background: "none", border: "none", color: "var(--text-faint)",
            fontSize: 12, cursor: "pointer", textAlign: "center", padding: 4,
          }}
        >
          Maybe later
        </button>
      </div>
    </div>
  );
}

export function hasUsageLimitModalBeenDismissed() {
  try { return sessionStorage.getItem(DISMISS_KEY) === "1"; } catch { return false; }
}
