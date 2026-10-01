/**
 * components/FixListPanel.jsx
 * 2026-10 — "Fix List" panel inside chat (founder spec, roadmap.sh-
 * validated gap: a vibecoder had to leave chat and go to the separate
 * /codebase-health page just to see what's still open). Reads the
 * real, un-gated list from GET /findings/fix-list (routers/
 * findings.py) and fires fixes through the SAME /fix-pipeline/bulk
 * endpoint + FixJobContext/FixProgressDrawer the Codebase Health page
 * already uses — no parallel fix machinery, no mocked data.
 */
import React, { useEffect, useState, useCallback } from "react";
import { ChevronDown, ChevronRight, Wrench, Loader2 } from "lucide-react";
import { api } from "../lib/api";

const SEV_DOT = {
  critical: "#ef4444",
  high:     "#f97316",
  medium:   "#facc15",
  low:      "#38bdf8",
  info:     "#94a3b8",
};

export default function FixListPanel({ projectId, visible = true }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [totalOpen, setTotalOpen] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busyIds, setBusyIds] = useState(() => new Set());

  const load = useCallback(() => {
    if (!projectId) return;
    setLoading(true);
    api.get("/findings/fix-list", { params: { project_id: projectId, limit: 20 } })
      .then((r) => {
        const d = r?.data || r;
        setItems(Array.isArray(d.items) ? d.items : []);
        setTotalOpen(d.total_open || 0);
      })
      .catch(() => { /* soft-fail — panel just stays empty */ })
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  // Live-remove an item the moment FixJobContext confirms a real
  // successful commit — same global event CodebaseHealth.jsx listens
  // to, so both surfaces stay in sync with zero extra plumbing.
  useEffect(() => {
    const onFixed = (e) => {
      const fid = e?.detail?.finding_id;
      if (!fid) return;
      setItems((prev) => prev.filter((it) => it.finding_id !== fid));
      setTotalOpen((n) => Math.max(0, n - 1));
    };
    window.addEventListener("aurem:finding-fixed", onFixed);
    return () => window.removeEventListener("aurem:finding-fixed", onFixed);
  }, []);

  if (!visible || !projectId || (!loading && items.length === 0)) return null;

  async function fixItem(it) {
    if (busyIds.has(it.finding_id)) return;
    setBusyIds((s) => new Set([...s, it.finding_id]));
    try {
      const r = await api.post("/fix-pipeline/bulk", {
        project_id: projectId,
        tool: "fix-list-panel",
        findings: [{ ...it, id: it.finding_id }],
      });
      const payload = r?.data || r;
      if (payload?.job_id) {
        window.dispatchEvent(new CustomEvent("aurem:open-fix-progress", {
          detail: { job_id: payload.job_id, total: 1 },
        }));
      }
    } catch {
      /* FixProgressDrawer / the catch in CodebaseHealth's pattern
         already surfaces quota/tier errors elsewhere; keep this
         panel's failure mode silent-but-safe (item just stays put,
         user can retry). */
    } finally {
      setBusyIds((s) => { const n = new Set(s); n.delete(it.finding_id); return n; });
    }
  }

  return (
    <div data-testid="fix-list-panel" style={{
      margin: "10px 0", borderRadius: 12,
      border: "1px solid rgba(148,163,184,0.18)",
      background: "rgba(15,23,42,0.55)", overflow: "hidden",
    }}>
      <button
        data-testid="fix-list-panel-toggle"
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 10,
          padding: "10px 14px", background: "transparent", border: "none",
          cursor: "pointer", color: "#e2e8f0", fontSize: 13, fontWeight: 600,
        }}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Wrench size={14} style={{ color: "#fbbf24" }} />
        <span>Fix List</span>
        <span style={{
          marginLeft: "auto", fontSize: 12, color: "#94a3b8",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {loading ? <Loader2 size={12} className="anim-spin" /> : `${totalOpen} open`}
        </span>
      </button>
      {open && (
        <div style={{ padding: "0 14px 12px" }}>
          {items.map((it) => (
            <div key={it.finding_id} data-testid={`fix-list-item-${it.finding_id}`}
                 style={{
                   display: "flex", alignItems: "center", gap: 10,
                   padding: "8px 0", borderTop: "1px solid rgba(148,163,184,0.10)",
                 }}>
              <span style={{
                width: 8, height: 8, borderRadius: 999, flexShrink: 0,
                background: SEV_DOT[it.severity] || SEV_DOT.medium,
              }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, color: "#e2e8f0", fontWeight: 500 }}>
                  {it.title || it.rule_id}
                </div>
                {it.file && (
                  <div style={{
                    fontSize: 11, color: "#64748b", fontFamily: "'JetBrains Mono', monospace",
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  }}>
                    {it.file}{it.line ? `:${it.line}` : ""}
                  </div>
                )}
              </div>
              <button
                data-testid={`fix-list-item-fix-btn-${it.finding_id}`}
                onClick={() => fixItem(it)}
                disabled={busyIds.has(it.finding_id)}
                style={{
                  padding: "4px 10px", borderRadius: 999, fontSize: 11, fontWeight: 600,
                  background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.35)",
                  color: "#86efac", cursor: busyIds.has(it.finding_id) ? "default" : "pointer",
                  opacity: busyIds.has(it.finding_id) ? 0.6 : 1, flexShrink: 0,
                }}>
                {busyIds.has(it.finding_id) ? "Fixing…" : "Fix"}
              </button>
            </div>
          ))}
          {totalOpen > items.length && (
            <div style={{ fontSize: 11, color: "#64748b", marginTop: 8 }}>
              + {totalOpen - items.length} more — open the full Codebase Health scan to see everything.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
