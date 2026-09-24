/**
 * components/ArchitectBlueprintPanel.jsx — Phase 4 (Architect Agent
 * ticket, "What I set up" panel), approved 2026-09-13.
 *
 * Read-only. Reads DIRECTLY from GET /architect/blueprint/{project_id}
 * (which itself reads directly from `project_blueprints` — no
 * separate summarisation layer), so this panel can never drift from
 * what the Architect Agent actually decided.
 *
 * Visually modelled on AdminIntegrations.jsx's card/status-badge
 * pattern (color-coded status pills, CSS-var themed) per the
 * founder's explicit ask to reuse that page's visual language
 * rather than invent a new UI system — adapted here to the app's
 * `.card` class convention already used throughout Projects.jsx.
 *
 * Type B "resolve" actions are a deliberate follow-up ticket — this
 * phase is read-only transparency only.
 */
import React, { useEffect, useState, useCallback } from "react";
import { CheckCircle2, Clock } from "lucide-react";
import { api } from "../lib/api";

export default function ArchitectBlueprintPanel({ projectId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true); setErr("");
    try {
      const r = await api.get(`/architect/blueprint/${projectId}`);
      setData(r.data);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Could not load setup info");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <div className="card" data-testid="architect-blueprint-panel-loading">
        <span className="eyebrow">what I set up</span>
        <p style={{ fontSize: 12, color: "var(--text-faint)", marginTop: 8 }}>Loading…</p>
      </div>
    );
  }

  if (err) {
    return (
      <div className="card" data-testid="architect-blueprint-panel-error">
        <span className="eyebrow">what I set up</span>
        <p style={{ fontSize: 12, color: "var(--danger)", marginTop: 8 }}>{err}</p>
      </div>
    );
  }

  // Normal, expected state — the Architect Agent hasn't run for this
  // project (e.g. created before this feature, or every task so far
  // was a scoped fix rather than a new build).
  if (!data || !data.exists) {
    return (
      <div className="card" data-testid="architect-blueprint-panel-empty">
        <span className="eyebrow">what I set up</span>
        <p style={{ fontSize: 12, color: "var(--text-faint)", marginTop: 8 }}>
          No setup decisions recorded yet for this project.
        </p>
      </div>
    );
  }

  const decisions = data.blueprint?.decisions || [];
  const typeA = decisions.filter((d) => d.type === "A");
  const typeBPending = data.type_b_pending || [];

  return (
    <div className="card" data-testid="architect-blueprint-panel">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <span className="eyebrow">what I set up</span>
        <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
          from: &ldquo;{data.idea}&rdquo;
        </span>
      </div>

      {typeA.length > 0 && (
        <div data-testid="architect-type-a-section" style={{ marginBottom: typeBPending.length ? 16 : 0 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: ".06em", marginBottom: 8 }}>
            Decided automatically
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {typeA.map((d, i) => (
              <div
                key={`a-${i}`}
                data-testid={`architect-decision-a-${i}`}
                data-decision-field={d.field}
                style={{
                  display: "flex", alignItems: "flex-start", gap: 8,
                  padding: "8px 10px", borderRadius: 6,
                  background: "rgba(109,212,161,0.06)",
                  border: "1px solid rgba(109,212,161,0.20)",
                }}
              >
                <CheckCircle2 size={14} style={{ color: "var(--ok, #6dd4a1)", marginTop: 1, flexShrink: 0 }} />
                <div style={{ fontSize: 12, lineHeight: 1.4 }}>
                  <strong style={{ color: "var(--text)" }}>{d.field}</strong>
                  {": "}
                  <span style={{ color: "var(--text-dim)" }}>{d.value}</span>
                  {d.reasoning && (
                    <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 2 }}>{d.reasoning}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {typeBPending.length > 0 && (
        <div data-testid="architect-type-b-section">
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: ".06em", marginBottom: 8 }}>
            Waiting on your input
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {typeBPending.map((d, i) => (
              <div
                key={`b-${i}`}
                data-testid={`architect-decision-b-${i}`}
                data-decision-field={d.field}
                data-resolved={d.resolved ? "true" : "false"}
                style={{
                  display: "flex", alignItems: "flex-start", gap: 8,
                  padding: "8px 10px", borderRadius: 6,
                  background: d.resolved ? "rgba(109,212,161,0.06)" : "rgba(255,180,84,0.08)",
                  border: `1px solid ${d.resolved ? "rgba(109,212,161,0.20)" : "rgba(255,180,84,0.25)"}`,
                }}
              >
                {d.resolved
                  ? <CheckCircle2 size={14} style={{ color: "var(--ok, #6dd4a1)", marginTop: 1, flexShrink: 0 }} />
                  : <Clock size={14} style={{ color: "#ffb454", marginTop: 1, flexShrink: 0 }} />}
                <div style={{ fontSize: 12, lineHeight: 1.4, flex: 1 }}>
                  <strong style={{ color: "var(--text)" }}>{d.field}</strong>
                  {d.resolved ? (
                    <>
                      {": "}<span style={{ color: "var(--text-dim)" }}>{d.resolution}</span>
                    </>
                  ) : (
                    <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 2 }}>
                      Using safe default for now: <em>{d.safe_default}</em>
                    </div>
                  )}
                </div>
                {!d.resolved && (
                  <span style={{
                    fontSize: 10, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase",
                    color: "#ffb454", background: "rgba(255,180,84,0.12)",
                    padding: "2px 8px", borderRadius: 4, flexShrink: 0,
                  }}>Pending</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
