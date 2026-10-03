import React, { useRef, useState } from "react";
import { X, Loader2, CheckCircle2, AlertTriangle, ShieldAlert } from "lucide-react";
import useModalA11y from "../../hooks/useModalA11y";
import { Z_ACTIVE_MODAL } from "../../lib/zIndex";
import { STAGE_LABELS, STAGE_ORDER, SEVERITY_LABEL, verdictSentence } from "./copy";

const VERDICT_ICON = { ok: CheckCircle2, attention: AlertTriangle, problem: ShieldAlert };
const VERDICT_COLOR = { ok: "#34d399", attention: "#f59e0b", problem: "#f87171" };

function StageRow({ stage, state }) {
  const Icon = state?.verdict ? VERDICT_ICON[state.verdict] : null;
  return (
    <li data-testid={`checkup-stage-${stage}`} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0" }}>
      {Icon ? <Icon size={18} color={VERDICT_COLOR[state.verdict]} aria-hidden="true" />
        : state?.running ? <Loader2 size={18} className="animate-spin" aria-hidden="true" />
        : <span style={{ width: 18 }} />}
      <span style={{ flex: 1 }}>{STAGE_LABELS[stage]}</span>
      <span style={{ opacity: 0.7, fontSize: 13 }}>
        {state?.verdict === "ok" ? "Looks good" : state?.verdict ? `${state.open_count} to look at` : state?.running ? "Checking…" : "Waiting"}
      </span>
    </li>
  );
}

function PermissionMatrix({ permissions }) {
  const [dev, setDev] = useState(false);
  if (!permissions) return null;
  if (!permissions.supported) {
    return <p data-testid="checkup-permissions-unavailable" style={{ opacity: 0.75, fontSize: 13 }}>
      {permissions.reason || "The data access map isn't available for this kind of project yet."}
    </p>;
  }
  if (!permissions.matrix?.length) return null;
  return (
    <section aria-label="Who can see what">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>Who can see what</strong>
        <button type="button" onClick={() => setDev((v) => !v)} aria-pressed={dev}
          style={{ background: "none", border: "none", color: "inherit", textDecoration: "underline", cursor: "pointer", fontSize: 12 }}>
          {dev ? "Hide developer view" : "Developer view"}
        </button>
      </div>
      <table data-testid="checkup-permission-matrix" style={{ width: "100%", fontSize: 13, marginTop: 6 }}>
        <thead><tr><th align="left">Your data</th><th align="left">Visitors</th><th align="left">Signed-in users</th></tr></thead>
        <tbody>
          {permissions.matrix.map((r) => (
            <tr key={r.name}>
              <td>{r.name}</td><td>{r.visitors}</td><td>{r.signed_in}</td>
              {dev && <td data-testid="checkup-dev-cell" style={{ fontFamily: "monospace", opacity: 0.7 }}>
                {r.developer.table} · {r.developer.rls ? "lock on" : "lock off"}
              </td>}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function CheckupScreen({ state, onClose, onAnswer, onOpenFixList, onRerun, fixListEnabled }) {
  const ref = useRef(null);
  useModalA11y({ ref, isOpen: true, onClose });
  const { phase, stages, result, error } = state;
  const top = (result?.items || []).slice(0, 3);
  return (
    <div data-testid="checkup-screen" ref={ref} role="dialog" aria-modal="true" aria-labelledby="checkup-title" tabIndex={-1}
      style={{ position: "fixed", inset: 0, zIndex: Z_ACTIVE_MODAL, background: "rgba(0,0,0,0.6)",
               display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ background: "var(--bg-elevated, #12151c)", color: "var(--text, #e6e8ee)", borderRadius: 14,
                    padding: 24, width: "100%", maxWidth: 520, maxHeight: "90vh", overflowY: "auto", position: "relative" }}>
        <button type="button" aria-label="Close checkup" data-testid="checkup-close" onClick={onClose}
          style={{ position: "absolute", top: 12, right: 12, background: "none", border: "none", color: "inherit", cursor: "pointer" }}>
          <X size={18} aria-hidden="true" />
        </button>
        <h2 id="checkup-title" style={{ margin: "0 0 4px" }}>
          {phase === "done" ? "Your app's checkup" : "Checking your app…"}
        </h2>
        <p style={{ margin: "0 0 12px", opacity: 0.75, fontSize: 14 }}>
          {phase === "error" ? error
            : phase === "done" ? verdictSentence(result.score, result.items.length)
            : "This usually takes under a minute. You can keep chatting meanwhile."}
        </p>
        {phase !== "error" && (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {STAGE_ORDER.map((s) => <StageRow key={s} stage={s} state={stages[s]} />)}
          </ul>
        )}
        {phase === "done" && (
          <>
            <div data-testid="checkup-score" style={{ fontSize: 40, fontWeight: 700, margin: "12px 0 4px" }}>
              {result.score}<span style={{ fontSize: 16, opacity: 0.6 }}> / 100</span>
            </div>
            {top.length > 0 && (
              <ul data-testid="checkup-top-items" style={{ paddingLeft: 18 }}>
                {top.map((i) => (
                  <li key={i.id} style={{ marginBottom: 6 }}>
                    <strong>{i.title_plain}</strong> <em style={{ opacity: 0.7, fontSize: 12 }}>({SEVERITY_LABEL[i.severity]})</em>
                    <div style={{ fontSize: 13, opacity: 0.8 }}>{i.impact_plain}</div>
                  </li>
                ))}
              </ul>
            )}
            {(result.permissions?.questions || []).map((q) => (
              <div key={q.table} data-testid="checkup-question" style={{ margin: "10px 0", padding: 10, border: "1px solid rgba(255,255,255,0.15)", borderRadius: 8 }}>
                <div style={{ marginBottom: 6 }}>{q.text}</div>
                {q.options.map((o) => (
                  <button key={o.value} type="button" onClick={() => onAnswer(q.table, o.value)}
                    style={{ marginRight: 8, padding: "4px 10px", cursor: "pointer" }}>{o.label}</button>
                ))}
              </div>
            ))}
            <PermissionMatrix permissions={result.permissions} />
            {result.unverified?.length > 0 && (
              <p data-testid="checkup-unverified" style={{ fontSize: 13, opacity: 0.8 }}>
                Couldn't verify {result.unverified.length} {result.unverified.length === 1 ? "check" : "checks"} this time — they are not counted as passed.
              </p>
            )}
          </>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
          {phase === "done" && fixListEnabled && result.items.length > 0 && (
            <button type="button" data-testid="checkup-open-fix-list" onClick={onOpenFixList}>See everything to fix</button>
          )}
          {(phase === "done" || phase === "error") && (
            <button type="button" data-testid="checkup-rerun" onClick={onRerun}>Check again</button>
          )}
          <button type="button" data-testid="checkup-open-chat" onClick={onClose}>Open chat anyway</button>
        </div>
      </div>
    </div>
  );
}
