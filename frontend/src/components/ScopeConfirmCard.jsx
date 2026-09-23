import React, { useState } from "react";

/**
 * ScopeConfirmCard — Trust & Verification round, Enhancement 5
 * (Pre-Execution Scope Confirmation).
 *
 * Rendered on an assistant turn where `awaitingScopeConfirmation` is
 * true (routers/chat/worker.py's `_scope_confirmation_gate` short-
 * circuited BEFORE any tool call ran). The turn's own `content`
 * already carries the interpreted-scope summary text — this card
 * adds the three real actions on top of it:
 *   - Confirm and proceed  -> resends the EXACT same prompt with
 *     `scope_confirmed_proposal_id` set (ChatPanel.confirmScopeProposal)
 *   - Change scope         -> cancels the proposal, lets the user
 *     type a new/clarified message themselves
 *   - Cancel               -> cancels the proposal, never executes
 */
export default function ScopeConfirmCard({ proposalId, prompt, onConfirm, onCancel }) {
  const [busy, setBusy] = useState(false);
  const [resolved, setResolved] = useState(null); // "confirmed" | "cancelled" | "change"

  if (!proposalId) return null;

  const handleConfirm = () => {
    if (busy || resolved) return;
    setBusy(true);
    setResolved("confirmed");
    onConfirm?.(proposalId, prompt);
  };
  const handleCancel = (kind) => {
    if (busy || resolved) return;
    setBusy(true);
    setResolved(kind);
    onCancel?.(proposalId);
  };

  return (
    <div
      data-testid="scope-confirm-card"
      style={{
        marginTop: 10,
        padding: "10px 12px",
        borderRadius: 10,
        border: "1px solid var(--border)",
        borderLeft: "3px solid var(--accent-2, #e8a020)",
        background: "var(--panel-2)",
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: 0.3,
                    color: "var(--accent-2, #e8a020)", marginBottom: 6 }}>
        AWAITING SCOPE CONFIRMATION
      </div>
      {resolved ? (
        <div data-testid="scope-confirm-resolved" style={{ fontSize: 13, color: "var(--text-dim)" }}>
          {resolved === "confirmed" && "Confirmed — proceeding…"}
          {resolved === "cancelled" && "Cancelled — nothing was touched."}
          {resolved === "change" && "Okay — type the change you'd like and send it as a new message."}
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            data-testid="scope-confirm-proceed-btn"
            onClick={handleConfirm}
            disabled={busy}
            style={{
              padding: "6px 14px", borderRadius: 8, border: "none",
              background: "var(--accent-2, #e8a020)", color: "#1a1206",
              fontSize: 13, fontWeight: 600, cursor: busy ? "default" : "pointer",
              opacity: busy ? 0.6 : 1,
            }}
          >
            Confirm and proceed
          </button>
          <button
            data-testid="scope-confirm-change-btn"
            onClick={() => handleCancel("change")}
            disabled={busy}
            style={{
              padding: "6px 14px", borderRadius: 8,
              border: "1px solid var(--border)", background: "transparent",
              color: "var(--text-dim)", fontSize: 13, cursor: busy ? "default" : "pointer",
            }}
          >
            Change scope
          </button>
          <button
            data-testid="scope-confirm-cancel-btn"
            onClick={() => handleCancel("cancelled")}
            disabled={busy}
            style={{
              padding: "6px 14px", borderRadius: 8,
              border: "1px solid var(--border)", background: "transparent",
              color: "var(--text-faint)", fontSize: 13, cursor: busy ? "default" : "pointer",
            }}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
