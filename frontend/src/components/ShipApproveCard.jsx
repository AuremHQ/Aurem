/**
 * ShipApproveCard.jsx — R3 (this round), physical ship-UI unification.
 *
 * The ONE "Approve the fix" card, shared by all 3 ship entry points
 * (ShipDialog inline, LoopLiveFeed's ShippedRow, ShipConfirmModal).
 * Renders: PR mini-guide (first approval only) + optional before/after
 * diff summary + optional Vanguard pill + the canonical approve/
 * cancel/rollback/retry button row, all driven by `useShipState`'s
 * one canonical status.
 *
 * Each surface still owns its OWN async handlers (two genuinely
 * different backends — loop-engine vs CTO-task rollback) and passes
 * them in as props; this component owns ONLY the shared render.
 */
import React from "react";
import { Loader2, GitPullRequest, Undo2, RotateCcw, CheckCircle2 } from "lucide-react";
import PrMiniGuide from "./PrMiniGuide";
import { useShipState } from "../lib/useShipState";
import { shipStatusLabel } from "../lib/shipStatus";

export default function ShipApproveCard({
  testidPrefix = "ship-approve",
  status,
  diffSummary = null,
  vanguard = null,
  error = null,
  busy = false,
  onApprove,
  onCancel,
  onRollback,
  onRollbackPointerDown,
  onRetry,
  approveLabel = "Approve the fix",
  approveTitle,
  // P2-D (this round) — LoopLiveFeed's ShippedRow keeps its OWN legacy
  // testids (deeply locked by pre-existing tests) even though it now
  // renders through this shared card. Any key left unset falls back
  // to the normal `${testidPrefix}-x` pattern below.
  testidOverrides = {},
}) {
  const ship = useShipState(status);
  const isBusy = busy || ship.isBusy;
  const rollbackTestid = testidOverrides.rollback || `${testidPrefix}-rollback-btn`;
  const retryTestid = testidOverrides.retry || `${testidPrefix}-retry-btn`;
  const statusTestid = testidOverrides.status || `${testidPrefix}-status`;

  return (
    <div data-testid={testidPrefix} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {ship.canApprove && <PrMiniGuide />}

      {diffSummary && (
        <div data-testid={`${testidPrefix}-diff`} style={{
          fontSize: 12, color: "var(--text-dim, #B0B0B0)",
          padding: "6px 10px", background: "rgba(255,255,255,0.03)", borderRadius: 6,
        }}>
          {diffSummary}
        </div>
      )}

      {vanguard && (
        <div data-testid={`${testidPrefix}-vanguard`} style={{
          display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11,
          color: vanguard.critical ? "#EF4444" : "#22C55E", width: "fit-content",
        }}>
          <CheckCircle2 size={12} />
          Vanguard {vanguard.critical ? `flagged ${vanguard.critical} critical` : "clean"}
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {ship.canApprove && (
          <>
            <button
              data-testid={`${testidPrefix}-approve-btn`}
              disabled={isBusy}
              onClick={onApprove}
              title={approveTitle}
              style={{
                padding: "8px 16px", borderRadius: 8, border: "none",
                background: isBusy ? "#5A5A5A" : "#FF6608", color: "#0A0A0A",
                fontWeight: 700, fontSize: 13, cursor: isBusy ? "default" : "pointer",
                display: "inline-flex", alignItems: "center", gap: 6,
              }}
            >
              <GitPullRequest size={13} /> {approveLabel}
            </button>
            {onCancel && (
              <button data-testid={`${testidPrefix}-cancel-btn`} disabled={isBusy} onClick={onCancel}
                style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid var(--border)",
                          background: "transparent", color: "var(--text-dim)", fontSize: 13,
                          cursor: isBusy ? "default" : "pointer" }}>
                Cancel
              </button>
            )}
          </>
        )}

        {ship.isShipping && (
          <span data-testid={statusTestid} style={{ fontSize: 13, display: "inline-flex", gap: 6, alignItems: "center" }}>
            <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} />
            {shipStatusLabel(ship.canonical)}
          </span>
        )}

        {ship.isLive && (
          <>
            <span data-testid={statusTestid} style={{ fontSize: 13, color: "#22C55E", fontWeight: 600 }}>
              {shipStatusLabel(ship.canonical)}
            </span>
            {onRollback && (
              <button
                data-testid={rollbackTestid}
                onClick={onRollback}
                onPointerDown={onRollbackPointerDown}
                style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid var(--border)",
                          background: "transparent", color: "var(--text-dim)", fontSize: 12,
                          cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5 }}>
                <Undo2 size={12} /> Rollback
              </button>
            )}
          </>
        )}

        {ship.isRollingBack && (
          <button
            type="button"
            disabled
            data-testid={testidOverrides.rollback || statusTestid}
            style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid var(--border)",
                      background: "transparent", color: "var(--text-dim)", fontSize: 13,
                      cursor: "wait", opacity: 0.6,
                      display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} />
            {shipStatusLabel(ship.canonical)}
          </button>
        )}

        {ship.isRolledBack && (
          <span data-testid={testidOverrides.rollback || statusTestid} style={{ fontSize: 13, color: "var(--text-faint)" }}>
            {shipStatusLabel(ship.canonical)}
          </span>
        )}

        {ship.isFailed && (
          <>
            <span data-testid={statusTestid} style={{ fontSize: 13, color: "#EF4444" }}>
              {error || shipStatusLabel(ship.canonical)}
            </span>
            {onRetry && (
              <button data-testid={retryTestid} onClick={onRetry}
                style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid var(--border)",
                          background: "transparent", color: "#F5F5F5", fontSize: 12,
                          cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5 }}>
                <RotateCcw size={12} /> Retry
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
