/**
 * LoopActionCards.jsx — Iter 212m-63 (Loop Mode Phase D lite)
 *
 * Two small components for the live-loop UX:
 *
 *   <SelfHealIndicator>
 *     Slim inline strip that lights up while the engine is rewriting
 *     a file that failed ruff/eslint.  Animates a self-heal "spinning
 *     wrench" + "attempt n/2" copy.  Hidden when state isn't
 *     self-healing.
 *
 *   <UserActionCard>
 *     Renders when a phase pauses and demands explicit input
 *     (`requires_user_action: true` from the SSE event schema).
 *     Three primary actions — Try Again / Skip / Abort — plus an
 *     optional feedback note the engine forwards as
 *     /pause-response.feedback.
 *
 * Both components are pure render — the parent (ChatPanel) owns the
 * state machine + API calls.
 */
import React, { useState } from "react";
import { Wrench, Play, SkipForward, X, AlertTriangle, ShieldCheck, Rocket, Clock, RotateCcw } from "lucide-react";
import { useExpiryCountdown, formatCountdown } from "../hooks/useExpiryCountdown";


export function SelfHealIndicator({ visible, attempt = 1, max = 2,
                                    errorPreview, startedAt }) {
  // Feb 2026 · Per-attempt live timer. Founder ask:
  // "chip shows a running time counter for the CURRENT attempt
  // that resets to 0 at the start of every new attempt". `startedAt`
  // is the epoch-ms captured by ChatPanel when the current attempt
  // began; we tick every 500ms while visible so the seconds counter
  // climbs live, independent of the overall loop timer.
  const [nowMs, setNowMs] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!visible || !startedAt) return;
    const iv = setInterval(() => setNowMs(Date.now()), 500);
    return () => clearInterval(iv);
  }, [visible, startedAt]);
  const elapsedSec = (visible && startedAt)
    ? Math.max(0, Math.floor((nowMs - startedAt) / 1000)) : 0;
  if (!visible) return null;
  return (
    <div
      data-testid="self-heal-indicator"
      role="status"
      aria-live="polite"
      style={{
        display: "flex", alignItems: "center", gap: 10,
        margin: "8px 0",
        padding: "8px 14px",
        borderRadius: 10,
        background: "linear-gradient(90deg, rgba(168,85,247,0.10), rgba(99,102,241,0.06))",
        border: "1px solid rgba(168,85,247,0.40)",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        color: "#c4b5fd",
      }}
    >
      <Wrench size={13} className="anim-spin" />
      <span style={{ flex: 1, minWidth: 0 }}>
        Retrying automatically — attempt <strong>{attempt}/{max}</strong>: ORA is
        rewriting the file that failed{errorPreview ? "…" : ""}
      </span>
      {startedAt && (
        <span data-testid="self-heal-timer" style={{
          fontSize: 10, fontWeight: 700,
          color: "#a78bfa",
          fontFamily: "'JetBrains Mono', monospace",
          padding: "2px 6px",
          background: "rgba(168,85,247,0.10)",
          borderRadius: 4,
          whiteSpace: "nowrap",
        }}>{elapsedSec}s</span>
      )}
      {errorPreview && (
        <code style={{
          fontSize: 9.5,
          color: "#a78bfa",
          padding: "2px 6px",
          background: "rgba(168,85,247,0.10)",
          borderRadius: 4,
          maxWidth: 280,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>{errorPreview}</code>
      )}
      <style>{`
        .anim-spin { animation: spin 1.2s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}


// One shared shell for the loop gate cards: the SHIP human-review gate and
// the generic retry/skip/abort card differ only in accent, title, countdown
// colour, message, the optional <pre> block and their buttons.
const GATE_CARD_BASE = {
  margin: "10px 0",
  padding: 14,
  borderRadius: 12,
  display: "flex",
  flexDirection: "column",
  gap: 10,
  fontFamily: "'JetBrains Mono', monospace",
};

function GateCard({ testId, phase, ariaLabel, cardStyle, icon, title, secondsLeft, expired, countdownIdleColor, message, preTestId, preColor, preMaxHeight, preShown, preText, children }) {
  return (
    <div data-testid={testId} data-phase={phase} role="region" aria-label={ariaLabel} style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {icon}
        {title}
        {secondsLeft != null && (
          <span data-testid="user-action-countdown" className={secondsLeft <= 60 ? "aurem-countdown-urgent" : undefined} style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10.5, fontWeight: 700, color: secondsLeft <= 60 ? "#fda4af" : countdownIdleColor, fontFamily: "'JetBrains Mono', monospace" }}>
            <Clock size={11} />
            {expired ? "Waiting to expire…" : formatCountdown(secondsLeft)}
          </span>
        )}
      </div>
      <div style={{ fontSize: 11.5, color: "var(--text, #e8ecf3)", lineHeight: 1.5 }}>{message}</div>
      {preShown && (
        <pre data-testid={preTestId} style={{ margin: 0, padding: "8px 10px", background: "rgba(0,0,0,0.40)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 6, fontSize: 10.5, color: preColor, maxHeight: preMaxHeight, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {preText}
        </pre>
      )}
      {children}
    </div>
  );
}

/**
 * @param {object} props
 * @param {string} props.phase   — phase the loop paused on (verify/scan/etc.)
 * @param {string} props.message — the engine's human-readable reason
 * @param {string[]} [props.errors] — optional flattened error lines
 * @param {(action: 'retry'|'skip'|'abort', feedback?: string) => void} props.onAction
 */
export function UserActionCard({ phase, message, errors,
                                  onAction, busy,
                                  gateType, testsTouched, expiresAt }) {
  const [feedback, setFeedback] = useState("");
  const secondsLeft = useExpiryCountdown(expiresAt);
  // Round-2 PR (P0-3) — same ~65s race-window guard as ShipPendingCard
  // / PlanApprovalCard, applied to every action button in this card
  // (both the ship_human_review gate branch and the generic
  // retry/skip/abort branch below).
  const expired = secondsLeft != null && secondsLeft <= 0;
  // Iter 332 — dedicated SHIP human-review gate. Test files were
  // modified, so the engine paused for explicit approval. Generic
  // retry/skip buttons here soft-locked the engine; the ONLY valid
  // actions are Approve & Ship or Cancel.
    if (gateType === "ship_human_review") {
    return (
      <GateCard
        testId="ship-review-gate-card"
        phase={phase}
        ariaLabel="Human review required before ship"
        cardStyle={{ ...GATE_CARD_BASE, background: "linear-gradient(135deg, rgba(34,197,94,0.12), rgba(16,185,129,0.05))", border: "1px solid rgba(34,197,94,0.45)", boxShadow: "0 0 28px -10px rgba(34,197,94,0.5)" }}
        icon={<ShieldCheck size={14} color="#86efac" />}
        title={<strong style={{ fontSize: 12, color: "#86efac", letterSpacing: 0.4 }}>Paused for your review — this change touches test files</strong>}
        secondsLeft={secondsLeft}
        expired={expired}
        countdownIdleColor="#86efac"
        message={message}
        preTestId="ship-review-tests-touched"
        preColor="#86efac"
        preMaxHeight={120}
        preShown={Array.isArray(testsTouched) && testsTouched.length > 0}
        preText={Array.isArray(testsTouched) ? testsTouched.slice(0, 10).join("\n") + (testsTouched.length > 10 ? "\n...and " + (testsTouched.length - 10) + " more" : "") : ""}
      >
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <ActionBtn
            testid="loop-approve-ship-btn"
            Icon={Rocket}
            label={busy ? "Approving…" : "Approve & Ship"}
            tone="primary"
            disabled={busy || expired}
            onClick={() => onAction?.("approve_ship")}
          />
          <ActionBtn
            testid="loop-cancel-ship-btn"
            Icon={X}
            label="Cancel ship"
            tone="danger"
            disabled={busy || expired}
            onClick={() => onAction?.("cancel_ship")}
          />
        </div>
      </GateCard>
    );
  }
  return (
    <GateCard
      testId="user-action-card"
      phase={phase}
      ariaLabel="Loop paused — your input needed"
      cardStyle={{ ...GATE_CARD_BASE, background: "linear-gradient(135deg, rgba(244,63,94,0.10), rgba(244,114,182,0.05))", border: "1px solid rgba(244,63,94,0.40)", boxShadow: "0 0 28px -10px rgba(244,63,94,0.45)" }}
      icon={<AlertTriangle size={14} color="#fda4af" />}
      title={<strong style={{ fontSize: 12, color: "#fda4af", letterSpacing: 0.4 }}>Loop paused at <code style={{ color: "#fff" }}>{phase}</code> — your input needed</strong>}
      secondsLeft={secondsLeft}
      expired={expired}
      countdownIdleColor="#fb7185"
      message={message}
      preTestId="user-action-errors"
      preColor="#fda4af"
      preMaxHeight={140}
      preShown={Array.isArray(errors) && errors.length > 0}
      preText={Array.isArray(errors) ? errors.slice(0, 12).join("\n") + (errors.length > 12 ? "\n...and " + (errors.length - 12) + " more" : "") : ""}
    >
      <textarea
        data-testid="user-action-feedback"
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        placeholder="Optional note for ORA (e.g. 'try a different library')"
        rows={2}
        disabled={busy}
        style={{ width: "100%", resize: "vertical", background: "rgba(255,255,255,0.04)", color: "inherit", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8, padding: 8, fontSize: 12, fontFamily: "inherit" }}
      />
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <ActionBtn
          testid="loop-retry-btn"
          Icon={Play}
          label={busy ? "Retrying…" : "Try a different approach"}
          tone="primary"
          disabled={busy || expired}
          onClick={() => onAction?.("retry", feedback || undefined)}
        />
        <ActionBtn
          testid="loop-skip-btn"
          Icon={SkipForward}
          label="Skip this step"
          tone="warn"
          disabled={busy || expired}
          onClick={() => onAction?.("skip", feedback || undefined)}
        />
        <ActionBtn
          testid="loop-abort-btn"
          Icon={X}
          label="Abort loop"
          tone="danger"
          disabled={busy || expired}
          onClick={() => onAction?.("abort", feedback || undefined)}
        />
      </div>
    </GateCard>
  );
}


/**
 * <LoopExpiredCard> — BUILD PROMPT v4 · Phase C · D1 (2026-08-27).
 *
 * D1 (founder-locked, Phase 0): a loop that timed out waiting for the
 * user's plan/ship approval renders as a NEUTRAL/blocked-family card
 * with an EXPLICIT "Expired" label — never red, never a spinner, and
 * never just a chip that quietly vanishes. Distinct from LoopFailureCard
 * (which is a real crash) — this is "nothing went wrong, time just ran
 * out"; the copy and tone must never suggest failure.
 *
 * Rehydrates on reload — see ChatPanel.jsx's expired-loop localStorage
 * marker + confirm-via-GET-/loop/{id}/status flow.
 */
export function LoopExpiredCard({ phase, onRestart, onDismiss }) {
  return (
    <div
      data-testid="loop-expired-card"
      data-phase={phase}
      role="status"
      aria-live="polite"
      aria-label="Loop session expired — awaiting your decision"
      style={{
        margin: "10px 0",
        padding: 14,
        background: "linear-gradient(135deg, rgba(148,163,184,0.10), rgba(148,163,184,0.04))",
        border: "1px solid rgba(148,163,184,0.35)",
        borderRadius: 12,
        display: "flex", flexDirection: "column", gap: 10,
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Clock size={14} color="#cbd5e1" />
        <strong style={{ fontSize: 12, color: "#cbd5e1", letterSpacing: 0.4 }}>
          Expired
        </strong>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--text-dim, #b7bfcc)", lineHeight: 1.5 }}>
        This session expired while waiting for your approval.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <ActionBtn
          testid="loop-expired-restart-btn"
          Icon={RotateCcw} label="Restart loop"
          tone="primary"
          onClick={onRestart}
        />
        <ActionBtn
          testid="loop-expired-dismiss-btn"
          Icon={X} label="Dismiss"
          tone="danger"
          onClick={onDismiss}
        />
      </div>
    </div>
  );
}


function ActionBtn({ testid, Icon, label, tone, disabled, onClick }) {
  const colors = {
    primary: { bg: "linear-gradient(135deg, #22c55e, #16a34a)",
               color: "#0a0a0a", border: "transparent",
               shadow: "0 6px 16px -8px rgba(34,197,94,0.6)" },
    warn:    { bg: "rgba(249,115,22,0.10)",
               color: "#fdba74",
               border: "rgba(249,115,22,0.45)",
               shadow: "none" },
    danger:  { bg: "transparent",
               color: "#fda4af",
               border: "rgba(244,63,94,0.45)",
               shadow: "none" },
  }[tone] || { bg: "transparent", color: "#fff",
               border: "rgba(255,255,255,0.12)", shadow: "none" };
  return (
    <button
      type="button"
      data-testid={testid}
      disabled={disabled}
      onClick={onClick}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "7px 14px",
        background: colors.bg,
        color: colors.color,
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        fontSize: 11.5, fontWeight: 700,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
        boxShadow: colors.shadow,
      }}
    >
      <Icon size={12} />
      {label}
    </button>
  );
}
