import React, { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

/** Consent is not completion. Only the server's task/commit receipt is success. */
export default function ScopeConfirmCard({ proposalId, prompt, onConfirm, onCancel, disabled = false, executionReceipt }) {
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState(null);
  const [resolved, setResolved] = useState(null);
  const [error, setError] = useState(null);
  const lock = useRef(false);
  const testId = (name) => `scope-confirm-${name}-${proposalId}`;

  useEffect(() => {
    if (executionReceipt?.proposal_id === proposalId) setReceipt(executionReceipt);
  }, [executionReceipt, proposalId]);

  useEffect(() => {
    if (!proposalId) return;
    let active = true;
    api.get(`/verification/scope-confirm/${proposalId}`).then(({ data }) => {
      if (!active) return;
      if (data?.proposal?.execution_receipt) setReceipt(data.proposal.execution_receipt);
      else if (["cancelled", "expired"].includes(data?.proposal?.status)) setResolved(data.proposal.status);
    }).catch(() => { if (active) setError("Couldn't refresh this proposal. Confirmation will recheck its current status."); });
    return () => { active = false; };
  }, [proposalId]);

  if (!proposalId) return null;
  const committed = receipt?.status === "succeeded" && receipt.task_id
    && /^[a-f0-9]{40,64}$/i.test(receipt.commit_sha || "");

  async function act(kind) {
    if (lock.current || disabled || resolved || receipt) return;
    lock.current = true; setBusy(true); setError(null);
    try {
      if (kind === "confirm") {
        if (!onConfirm) throw new Error("Confirmation is unavailable. No execution was requested.");
        const result = await onConfirm(proposalId, prompt);
        if (!result || result.proposal_id !== proposalId || !["succeeded", "executing", "unknown", "failed"].includes(result.status)) {
          throw new Error("No execution receipt was received. Check this proposal before retrying.");
        }
        if (result.status === "succeeded" && (!result.task_id || !/^[a-f0-9]{40,64}$/i.test(result.commit_sha || ""))) {
          throw new Error("The response has no verifiable task/commit receipt.");
        }
        setReceipt(result);
      } else {
        const result = await onCancel?.(proposalId);
        if (result?.ok !== true || result.status !== "cancelled") {
          throw new Error("Cancellation was not confirmed. This proposal may already be executing.");
        }
        setResolved(kind === "change" ? "change" : "cancelled");
      }
    } catch (e) { setError(e?.message || "Confirmation failed. No success has been verified."); }
    finally { lock.current = false; setBusy(false); }
  }

  return (
    <div data-testid={testId("card")} style={{ marginTop: 10, padding: 12, borderRadius: 8,
      border: "1px solid var(--border)", borderLeft: "3px solid var(--accent-2, #e8a020)", background: "var(--panel-2)",
      overflowWrap: "anywhere", maxWidth: "100%", minWidth: 0 }}>
      <div data-testid={testId("status")} role="status" style={{ fontSize: 13, marginBottom: 8 }}>
        {busy ? "Waiting for the execution receipt…" : committed ? `Commit recorded: ${receipt.commit_sha}`
          : receipt ? `${receipt.status === "failed" ? "Execution failed" : "Execution not yet verified"}. ${receipt.error || "Check the task record."}`
          : resolved === "change" ? "Cancelled. Send the revised scope as a new request."
          : resolved ? `Proposal ${resolved}.` : "Awaiting scope confirmation"}
      </div>
      {receipt?.task_id && <div data-testid={testId("task-id")}>Task: <code>{receipt.task_id}</code></div>}
      {error && <div role="alert" data-testid={testId("error")} style={{ color: "var(--error, #ef4444)", marginBottom: 8 }}>{error}</div>}
      {!resolved && !receipt && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" data-testid={testId("proceed-btn")} className="btn-primary" disabled={busy || disabled} onClick={() => act("confirm")}>Confirm and proceed</button>
        <button type="button" data-testid={testId("change-btn")} className="btn-ghost" disabled={busy || disabled} onClick={() => act("change")}>Change scope</button>
        <button type="button" data-testid={testId("cancel-btn")} className="btn-ghost" disabled={busy || disabled} onClick={() => act("cancel")}>Cancel</button>
      </div>}
    </div>
  );
}