/**
 * pages/AdminAuditPage.jsx — Admin "Audit feed" tab.
 *
 * 2026-08-27 · Admin Compact M6 — extracted verbatim from Admin.jsx's
 * inline AuditPage() (+ its private th/td style consts) so this tab
 * code-splits into its own chunk. Behavior is unchanged.
 *
 * 2026-09-07 — added an "Admin actions" sub-tab (bulk-revoke,
 * flag-idle, etc. from `db.admin_audit` via GET /admin/audit/actions)
 * alongside the original "ORA turns" sub-tab (`db.ora_audit` via
 * GET /admin/audit). Two unrelated collections/shapes that both
 * count as "the audit log" conceptually — kept as separate sub-tabs
 * on the SAME page/sidebar entry rather than merging their shapes.
 */
import React, { useState, useEffect } from "react";
import { api } from "../lib/api";

function AdminActionsTab() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [err, setErr] = useState("");

  async function load() {
    setLoading(true); setErr("");
    try {
      const r = await api.get("/admin/audit/actions?limit=100");
      setRows(r.data?.rows || []);
    } catch (e) {
      setErr(e?.response?.data?.detail?.message || e?.response?.data?.detail || e.message || "Failed to load admin actions");
    } finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);

  return (
    <div data-testid="admin-audit-actions-tab">
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 10 }}>
        <button onClick={load} data-testid="admin-audit-actions-refresh"
                className="btn-ghost" style={{ fontSize: 11 }}>
          Refresh
        </button>
      </div>
      {loading && <div style={{ color: "var(--text-faint)", fontSize: 12 }}>Loading…</div>}
      {err && <div style={{ color: "#ef4444", fontSize: 12 }}>{err}</div>}
      {!loading && rows.length === 0 && !err && (
        <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
          No admin actions logged yet (bulk-revoke, flag-idle, etc.).
        </div>
      )}
      {rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table data-testid="admin-audit-actions-table" style={{
            width: "100%", borderCollapse: "collapse", fontSize: 11,
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            <thead>
              <tr style={{ color: "var(--text-faint)", textAlign: "left",
                            borderBottom: "1px solid var(--border)" }}>
                <th style={th}>Timestamp</th>
                <th style={th}>Admin</th>
                <th style={th}>Action</th>
                <th style={th}>Installations</th>
                <th style={th}>Users</th>
                <th style={th}>Reason</th>
                <th style={th}>Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const key = `${r.action}-${r.ts}-${i}`;
                const isOpen = expanded === key;
                return (
                  <React.Fragment key={key}>
                    <tr
                      data-testid={`admin-audit-actions-row-${i}`}
                      onClick={() => setExpanded(isOpen ? null : key)}
                      style={{
                        cursor: "pointer",
                        borderBottom: "1px solid rgba(255,255,255,0.04)",
                        background: isOpen ? "var(--bg-elev)" : "transparent",
                        color: "var(--text-dim)",
                      }}
                    >
                      <td style={td}>{(r.ts || "").slice(0, 19).replace("T", " ")}</td>
                      <td style={td}>{r.actor || "—"}</td>
                      <td style={td}>{r.action || "—"}</td>
                      <td style={td}>{(r.installation_ids || []).length}</td>
                      <td style={td}>{(r.user_ids || []).length}</td>
                      <td style={td} title={r.reason || ""}>{(r.reason || "—").slice(0, 30)}</td>
                      <td style={td}>{r.result ? JSON.stringify(r.result) : "—"}</td>
                    </tr>
                    {isOpen && (
                      <tr data-testid={`admin-audit-actions-detail-${i}`}>
                        <td colSpan={7} style={{
                          padding: "10px 14px",
                          background: "var(--bg-elev)",
                          borderBottom: "1px solid var(--border)",
                          color: "var(--text-dim)",
                          whiteSpace: "pre-wrap", wordBreak: "break-word",
                        }}>
                          <div><strong style={{ color: "var(--text)" }}>installation_ids:</strong> {(r.installation_ids || []).join(", ") || "—"}</div>
                          <div><strong style={{ color: "var(--text)" }}>user_ids:</strong> {(r.user_ids || []).join(", ") || "—"}</div>
                          <div><strong style={{ color: "var(--text)" }}>reason:</strong> {r.reason || "—"}</div>
                          {r.per_installation ? <div><strong style={{ color: "var(--text)" }}>per_installation:</strong> {JSON.stringify(r.per_installation)}</div> : null}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Iter 210 — Audit feed page
// Hits GET /admin/audit and renders one row per ORA turn. Plain
// dark-theme table matching the rest of the panel.
// ──────────────────────────────────────────────────────────────────
export default function AdminAuditPage() {
  const [sub, setSub] = useState("turns");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [err, setErr] = useState("");
  const [signalsOnly, setSignalsOnly] = useState(false);

  async function load(signalsOnlyOverride) {
    const wantSignalsOnly = signalsOnlyOverride ?? signalsOnly;
    setLoading(true); setErr("");
    try {
      const r = await api.get(
        `/admin/audit?limit=100&signals_only=${wantSignalsOnly ? "true" : "false"}`
      );
      setRows(r.data?.rows || []);
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message || "Failed to load audit feed");
    } finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function toggleSignalsOnly() {
    const next = !signalsOnly;
    setSignalsOnly(next);
    load(next);
  }

  return (
    <div data-testid="admin-audit-page" style={{ padding: 24 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: 16, color: "var(--text)" }}>Audit feed</h2>
          <div style={{ display: "flex", gap: 6 }}>
            <button data-testid="admin-audit-subtab-turns"
                    onClick={() => setSub("turns")}
                    style={{
                      padding: "3px 8px", borderRadius: 3, fontSize: 10, cursor: "pointer",
                      border: "1px solid " + (sub === "turns" ? "var(--accent)" : "var(--border)"),
                      background: sub === "turns" ? "var(--accent-soft)" : "transparent",
                      color: sub === "turns" ? "var(--accent)" : "var(--text-faint)",
                    }}>ORA turns</button>
            <button data-testid="admin-audit-subtab-actions"
                    onClick={() => setSub("actions")}
                    style={{
                      padding: "3px 8px", borderRadius: 3, fontSize: 10, cursor: "pointer",
                      border: "1px solid " + (sub === "actions" ? "var(--accent)" : "var(--border)"),
                      background: sub === "actions" ? "var(--accent-soft)" : "transparent",
                      color: sub === "actions" ? "var(--accent)" : "var(--text-faint)",
                    }}>Admin actions</button>
          </div>
        </div>
        {sub === "turns" && (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <label
              data-testid="admin-audit-signals-only-toggle"
              style={{
                display: "flex", alignItems: "center", gap: 5,
                fontSize: 11, color: signalsOnly ? "#ef4444" : "var(--text-faint)",
                cursor: "pointer", userSelect: "none",
              }}
              title="Show only turns that ever emitted a system signal (github_auth_failed, repo_not_found, etc.) — these are rare, so the default newest-first list almost never shows one."
            >
              <input
                type="checkbox"
                checked={signalsOnly}
                onChange={toggleSignalsOnly}
                data-testid="admin-audit-signals-only-checkbox"
              />
              ⚠ Signals only
            </label>
            <button onClick={() => load()} data-testid="admin-audit-refresh"
                    className="btn-ghost" style={{ fontSize: 11 }}>
              Refresh
            </button>
          </div>
        )}
      </div>

      {sub === "actions" ? (
        <AdminActionsTab />
      ) : (
        <>
      {loading && <div style={{ color: "var(--text-faint)", fontSize: 12 }}>Loading…</div>}
      {err && <div style={{ color: "#ef4444", fontSize: 12 }}>{err}</div>}
      {!loading && rows.length === 0 && !err && (
        <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
          {signalsOnly
            ? "No turns have ever emitted a system signal in this range — that's expected, they're rare (github_auth_failed, repo_not_found, etc.)."
            : "No audit rows yet. Have a user chat with ORA and refresh."}
        </div>
      )}
      {rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
        <table data-testid="admin-audit-table" style={{
          width: "100%", borderCollapse: "collapse", fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          <thead>
            <tr style={{ color: "var(--text-faint)", textAlign: "left",
                          borderBottom: "1px solid var(--border)" }}>
              <th style={th}>Timestamp</th>
              <th style={th}>User</th>
              <th style={th}>Project</th>
              <th style={th}>Tools</th>
              <th style={th} title="Citation guard triggered?">🛡️</th>
              <th style={th}>⚠️ Signals</th>
              <th style={th}>Model</th>
              <th style={th}>Retry</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isOpen = expanded === r.turn_id;
              const sigs = r.system_signals_emitted || [];
              return (
                <React.Fragment key={r.turn_id}>
                  <tr
                    data-testid={`admin-audit-row-${r.turn_id}`}
                    onClick={() => setExpanded(isOpen ? null : r.turn_id)}
                    style={{
                      cursor: "pointer",
                      borderBottom: "1px solid rgba(255,255,255,0.04)",
                      background: isOpen ? "var(--bg-elev)" : "transparent",
                      color: "var(--text-dim)",
                    }}
                  >
                    <td style={td}>{(r.timestamp || "").slice(0, 19).replace("T", " ")}</td>
                    <td style={td} title={r.user_id}>{(r.user_id || "").slice(0, 10) + "…"}</td>
                    <td style={td} title={r.project_id || ""}>{(r.project_id || "—").slice(0, 14)}</td>
                    <td style={td}>{(r.tools_called || []).length}</td>
                    <td style={td}>
                      {r.citation_guard_triggered
                        ? <span style={{ color: "#f59e0b" }}>YES</span>
                        : <span style={{ color: "#22c55e" }}>—</span>}
                    </td>
                    <td style={td}>
                      {sigs.length === 0
                        ? <span style={{ color: "var(--text-faint)" }}>—</span>
                        : <span style={{ color: "#ef4444" }}>{sigs.join(", ")}</span>}
                    </td>
                    <td style={td}>{r.llm_model || "—"}</td>
                    <td style={td}>{r.was_retry ? "↻" : "—"}</td>
                  </tr>
                  {isOpen && (
                    <tr data-testid={`admin-audit-detail-${r.turn_id}`}>
                      <td colSpan={8} style={{
                        padding: "10px 14px",
                        background: "var(--bg-elev)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-dim)",
                        whiteSpace: "pre-wrap", wordBreak: "break-word",
                      }}>
                        <div><strong style={{ color: "var(--text)" }}>turn_id:</strong> {r.turn_id}</div>
                        <div><strong style={{ color: "var(--text)" }}>tools_called:</strong> {(r.tools_called || []).join(" · ") || "—"}</div>
                        <div><strong style={{ color: "var(--text)" }}>citation_guard_paths_fetched:</strong> {(r.citation_guard_paths_fetched || []).join(", ") || "—"}</div>
                        <div><strong style={{ color: "var(--text)" }}>citation_guard_unverified:</strong> {(r.citation_guard_unverified || []).join(", ") || "—"}</div>
                        <div><strong style={{ color: "var(--text)" }}>response_tokens:</strong> {r.response_tokens || 0}</div>
                        {r.extra ? <div><strong style={{ color: "var(--text)" }}>extra:</strong> {JSON.stringify(r.extra)}</div> : null}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
        </>
      )}
    </div>
  );
}
const th = { padding: "8px 10px", fontWeight: 500 };
const td = { padding: "7px 10px" };
