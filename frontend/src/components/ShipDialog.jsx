/**
 * ShipDialog.jsx — Inline "Approve the fix" action row rendered below an
 * assistant message that contains an `aurem-handoff` brief.
 *
 * Renders one of:
 *   • Idle      — 🚀 Approve the fix button (+ Maxx chip + ShipLintBadge)
 *   • Shipping  — disabled button with spinner
 *   • Shipped   — TaskProgressCard (live progress / commit / rollback)
 *   • Disabled  — italic hint (tokens exhausted / no active project)
 *   • Error     — red error text
 *
 * Purely presentational: parent (MessageBubble) owns the state machine
 * and passes callbacks.
 *
 * Iter 62: extracted from ChatPanel.jsx as part of the P1 split.
 */
import React from "react";
import { Rocket } from "lucide-react";
import ShipLintBadge from "./ShipLintBadge";
import TaskProgressCard from "./TaskProgressCard";
import TaskLiveTape from "./TaskLiveTape";
import ShipApproveCard from "./ShipApproveCard";

export default function ShipDialog({
  idx,
  msg,
  handoffBrief,
  canShip,
  exhausted,
  shipState,
  taskInfo,
  activeProject,
  onShip,
  onRollback,
  onOpenDeployTab,
  onOpenLivePopup,
}) {
  if (!handoffBrief) return null;

  // Iter 212m-9 — "Code shipped — ready to go live?" reminder. Renders
  // whenever the shipped task is fully done so the user can jump
  // straight to the Deploy panel without hunting for the toolbar
  // button.
  const showDeployBanner = (
    shipState.status === "shipped"
    && taskInfo?.status === "done"
    // 2026-09-11 — real QA repro: after a successful rollback, this
    // banner kept saying "Code shipped — ready to go live?" even
    // though the shipped commit had just been reverted — taskInfo's
    // `status: "done"` reflects the SHIP task's own lifecycle (it
    // finished), not whether that code is still the current state.
    // `rollback_status: "done"` (services/loop_rollback.py) is set
    // once a rollback actually completes — exclude it here.
    && taskInfo?.rollback_status !== "done"
    && !!activeProject?.project_id
    && typeof onOpenDeployTab === "function"
  );

  return (
    <div data-testid={`ship-cto-row-${idx}`} style={{
      marginTop: 10, paddingLeft: 4,
      display: "flex", alignItems: "stretch", gap: 10, flexWrap: "wrap",
      flexDirection: "column",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      {!canShip ? (
        <div style={{ fontSize: 11, color: "var(--text-faint)", fontStyle: "italic" }}>
          {exhausted
            ? "🚫 Tokens exhausted — upgrade your plan to approve fixes."
            : "Switch to a connected project to approve fixes."}
        </div>
      ) : shipState.status === "shipped" ? (
        <div style={{ flex: 1, minWidth: 0 }}>
          <TaskLiveTape taskId={shipState.taskId} />
          <TaskProgressCard
            taskId={shipState.taskId}
            task={taskInfo}
            project={activeProject}
            onRollback={onRollback}
            onOpenLivePopup={onOpenLivePopup}
          />
        </div>
      ) : (
        <>
          {/* R3 — physical ship-UI unification: the ONE shared
              approve/retry card + useShipState machine, same
              component ShipConfirmModal now uses. */}
          <ShipApproveCard
            testidPrefix={`ship-cto-${idx}`}
            status={shipState.status}
            error={shipState.error}
            onApprove={onShip}
            onRetry={onShip}
            approveLabel="🚀 Approve the fix"
            approveTitle={`Ship to ${activeProject.github_owner}/${activeProject.github_repo}@${activeProject.branch}`}
          />
          {/* 2026-09-17 — Bug 3 fix (live QA report): "Approve the fix"
              used to render next to "plan clean ✓" / a "double-checked"
              claim with literally nothing else on screen — the code
              doesn't exist yet at this stage (it's generated AFTER
              you click Approve, then independently gated by the
              hallucination/import/syntax/security checks before any
              push). Rather than disable the button (that would break
              this entire approve-then-generate flow, which has no
              diff to show by design at this point), say so plainly —
              same "honest disclosure over a hidden gap" standard as
              the "plan clean ✓" relabel below. The real diff renders
              in TaskProgressCard once shipping starts, and is
              persisted to the task (survives a reload) — see
              services/task_diff.py + cto_tasks.files_changed. */}
          <div
            data-testid={`ship-no-diff-yet-${idx}`}
            style={{ fontSize: 11, color: "var(--text-faint)", fontStyle: "italic" }}
          >
            No diff to review yet — the code is written after you approve,
            then checked and shown here before it's pushed.
          </div>
          {/* Iter 47 — Maxx mode chip + brief lint preview */}
          {msg.maxxMode && (
            <span
              data-testid={`ship-maxx-chip-${idx}`}
              title="Maxx mode — the strongest single model writes this fix directly (security-scanned; no separate second-reviewer pass — see services/agents.py review_tail)"
              style={{
                padding: "3px 8px", borderRadius: 4,
                fontSize: 10, fontWeight: 700,
                letterSpacing: "0.05em",
                fontFamily: "'JetBrains Mono', monospace",
                background: "var(--accent-soft)",
                color: "var(--accent-2)",
                border: "1px solid var(--border-strong)",
              }}
            >MAXX</span>
          )}
          <ShipLintBadge brief={handoffBrief} testidSuffix={idx} />
        </>
      )}
      </div>

      {showDeployBanner && (
        <div
          data-testid={`ship-deploy-banner-${idx}`}
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 14px",
            background: "linear-gradient(90deg, var(--accent-soft) 0%, transparent 100%)",
            border: "1px solid var(--accent-2)",
            borderRadius: 6,
            flexWrap: "wrap",
          }}
        >
          <Rocket size={14} color="var(--accent-2)" />
          <span style={{
            fontSize: 12, color: "var(--text)", fontWeight: 600,
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            🚀 Code shipped — ready to go live?
          </span>
          <span style={{
            fontSize: 11, color: "var(--text-dim)", flex: 1, minWidth: 120,
          }}>
            Push the merged commit to your server with one click.
          </span>
          <button
            data-testid={`ship-deploy-banner-btn-${idx}`}
            onClick={onOpenDeployTab}
            style={{
              padding: "6px 14px", fontSize: 12, fontWeight: 600,
              background: "var(--accent-2)", color: "var(--bg)",
              border: "1px solid var(--accent-2)", borderRadius: 4,
              cursor: "pointer",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.05em",
            }}
          >
            Open Deploy →
          </button>
        </div>
      )}
    </div>
  );
}
