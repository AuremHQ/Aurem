/**
 * RollbackConfirmModal.jsx — Iter 362 · Bug B fix
 *
 * Themed in-app modal replacing the native `window.confirm(...)` used
 * by ShippedRow (LoopLiveFeed.jsx) and OperationHistory row rollback
 * buttons.
 *
 * Founder-reported (retest, 2/2 reproductions):
 *   • Rollback triggered a native browser confirm() dialog that
 *     rendered pinned below the address bar in a plain-white
 *     Chromium box — breaking the app's dark theme.
 *   • window.confirm() synchronously blocks the JS thread until
 *     dismissed, hanging automated testing / accessibility tooling.
 *
 * This modal:
 *   • Renders in-app, dark theme, centered over the chat window.
 *   • Non-blocking — plays nice with async flows and testing.
 *   • Same message content as the removed confirm() dialog.
 *   • Focus-management + Escape-to-cancel for keyboard users.
 *   • Same `data-testid` contract expected by the QA suite:
 *       - `rollback-confirm-modal`      (root)
 *       - `rollback-confirm-approve`    (confirm button)
 *       - `rollback-confirm-cancel`     (cancel button)
 */
import React, { useRef } from "react";
import { AlertTriangle, RotateCcw, X } from "lucide-react";
import useModalA11y from "../hooks/useModalA11y";

export default function RollbackConfirmModal({
  open,
  shortLabel,     // "shipped commit abc1234" or "shipped loop 5f7a8..."
  onConfirm,
  onCancel,
  // Part XIII · 1 — drift-acknowledge second confirm. When the
  // backend answered rollback_status "drift_detected", the caller
  // passes `drift` = { branch, expected, current, detail } and the
  // modal becomes the explicit "branch has moved — acknowledge drift
  // and roll back anyway?" step instead of the plain confirm. The
  // confirm button then resends with acknowledge_drift: true (the
  // caller owns the resend — this modal only surfaces the choice).
  drift = null,   // null = plain first confirm; object = drift second confirm
  confirmLabel,   // override for the confirm button ("Roll back anyway")
}) {
  const confirmBtnRef = useRef(null);
  const modalRef = useRef(null);

  // Iter 388t · Bug 27 · use reusable focus-trap hook.  Replaces the
  // hand-rolled Escape listener + focus-timeout below with a WCAG-
  // compliant trap that also wraps Tab within the modal.
  useModalA11y({
    ref:          modalRef,
    isOpen:       open,
    onClose:      onCancel,
    initialFocus: confirmBtnRef,
  });

  if (!open) return null;

  return (
    <div
      data-testid="rollback-confirm-modal"
      ref={modalRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="rollback-confirm-title"
      tabIndex={-1}
      onClick={(e) => {
        // Backdrop click cancels (like other modals in the app).
        if (e.target === e.currentTarget) onCancel && onCancel();
      }}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.72)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10_000,
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(460px, 92vw)",
          background: "linear-gradient(135deg, #0f1419 0%, #131a24 100%)",
          border: "1px solid rgba(239, 68, 68, 0.35)",
          borderRadius: 14,
          padding: 22,
          display: "flex",
          flexDirection: "column",
          gap: 14,
          boxShadow: "0 24px 60px -12px rgba(239, 68, 68, 0.35), "
                   + "0 0 0 1px rgba(239, 68, 68, 0.1)",
        }}
      >
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <div style={{
            display: "inline-flex", alignItems: "center",
            justifyContent: "center",
            width: 32, height: 32, borderRadius: 8,
            background: "rgba(239, 68, 68, 0.12)",
            border: "1px solid rgba(239, 68, 68, 0.35)",
          }}>
            <AlertTriangle size={16} color="#f87171" strokeWidth={2.5} />
          </div>
          <strong
            id="rollback-confirm-title"
            style={{
              fontSize: 13.5, color: "#f87171", letterSpacing: 0.4,
              textTransform: "uppercase",
            }}
          >
            Rollback&nbsp;{shortLabel || "this shipped commit"}?
          </strong>
        </div>

        {drift ? (
          <div data-testid="rollback-confirm-drift-detail" style={{
            fontSize: 12, color: "#c2c9d6", lineHeight: 1.6,
          }}>
            <div style={{ color: "#fbbf24", fontWeight: 700, marginBottom: 6 }}>
              Branch has moved — acknowledge drift and roll back anyway?
            </div>
            <div>
              {drift.detail
                || "The branch changed since this fix was applied. "
                + "Rolling back now reverts the shipped commit on top of "
                + "the moved branch."}
            </div>
            {(drift.branch || drift.expected || drift.current) && (
              <div style={{
                marginTop: 8, padding: "8px 10px",
                background: "rgba(0,0,0,0.35)",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: 6, fontSize: 11,
              }}>
                {drift.branch && (<div>Branch: <code>{drift.branch}</code></div>)}
                {drift.expected && (<div>Expected: <code>{String(drift.expected).slice(0, 12)}</code></div>)}
                {drift.current && (<div>Current: <code>{String(drift.current).slice(0, 12)}</code></div>)}
              </div>
            )}
          </div>
        ) : (
        <div style={{
          fontSize: 12, color: "#c2c9d6", lineHeight: 1.6,
        }}>
          This creates a new <strong style={{ color: "#e6ebf3" }}>
          revert commit</strong> on GitHub that undoes the ship. No
          history is force-pushed — the original commit stays in the
          log for audit.
        </div>
        )}

        <div style={{
          display: "flex", gap: 8, flexWrap: "wrap",
          marginTop: 4,
        }}>
          <button
            ref={confirmBtnRef}
            type="button"
            data-testid="rollback-confirm-approve"
            onClick={onConfirm}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "8px 16px",
              background: "linear-gradient(135deg, #ef4444, #b91c1c)",
              color: "#fff", border: "none", borderRadius: 8,
              fontSize: 11.5, fontWeight: 700,
              cursor: "pointer",
              textTransform: "uppercase", letterSpacing: 0.04,
              boxShadow: "0 6px 18px -8px rgba(239, 68, 68, 0.7)",
              transition: "transform 120ms ease",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.transform = "translateY(-1px)")}
            onMouseLeave={(e) => (e.currentTarget.style.transform = "translateY(0)")}
          >
            <RotateCcw size={12} strokeWidth={2.5} />
            {confirmLabel || (drift ? "Roll back anyway" : "Rollback")}
          </button>
          <button
            type="button"
            data-testid="rollback-confirm-cancel"
            onClick={onCancel}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "8px 16px",
              background: "transparent",
              color: "#c2c9d6",
              border: "1px solid rgba(255, 255, 255, 0.15)",
              borderRadius: 8,
              fontSize: 11.5, fontWeight: 600,
              cursor: "pointer",
              textTransform: "uppercase", letterSpacing: 0.04,
            }}
          >
            <X size={12} strokeWidth={2.5} />
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
