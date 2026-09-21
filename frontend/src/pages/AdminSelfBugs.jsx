/**
 * pages/AdminSelfBugs.jsx — Item 3 (2026-08-31)
 *
 * Self-repair dashboard for P7's structured bug ledger (`ora_self_bugs`)
 * + the learned-recurrence counter (`self_bug_learned`). Fetches from
 * `GET /admin/self-bugs/list`.
 *
 * 2026-09-21 — Self-Repair audit (Part 3/4): the ledger rows themselves
 * (`ora_self_bugs`) are still append-only/immutable — the audit trail
 * never changes. Two narrow actions now exist on the learned-pattern
 * side: mark-fixed/re-open (Part 3), and approve/reject a PROPOSE-ONLY
 * fix suggestion once a bug recurs 5+ times (Part 4). Approving/
 * rejecting a proposal only records the human decision — nothing is
 * ever applied automatically; see services/self_bug.py::propose_fix.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api, getToken } from "../lib/api";
import { toast } from "../components/Toast";

const SEVERITY_COLOR = {
  high: "#ef4444",
  low: "#f59e0b",
};

function fmtWhen(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

export default function AdminSelfBugs() {
  const [type, setType] = useState("");
  const [types, setTypes] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busySig, setBusySig] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const h = { Authorization: `Bearer ${getToken()}` };
      const q = type ? `?type=${type}` : "";
      const r = await api.get(`/admin/self-bugs/list${q}`, { headers: h });
      setRows(r.data?.self_bugs || []);
      setTypes(r.data?.types || []);
    } catch (e) {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [type]);

  useEffect(() => { load(); }, [load]);

  const markFixed = (sig, status) => {
    setBusySig(sig);
    api.post("/admin/self-bugs/mark-fixed", { signature: sig, status })
      .then(() => { toast({ message: status === "fixed" ? "Marked fixed" : "Re-opened", kind: "success" }); load(); })
      .catch(() => toast({ message: "Failed to update status", kind: "error" }))
      .finally(() => setBusySig(null));
  };

  const reviewProposal = (sig, decision) => {
    setBusySig(sig);
    api.post("/admin/self-bugs/review-proposal", { signature: sig, decision })
      .then(() => { toast({ message: decision === "approved" ? "Proposal approved" : "Proposal rejected", kind: "success" }); load(); })
      .catch(() => toast({ message: "Failed to record review", kind: "error" }))
      .finally(() => setBusySig(null));
  };

  return (
    <div style={{ padding: "24px 20px", maxWidth: 1200 }}
         data-testid="admin-self-bugs-panel">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18, gap: 12, flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0, color: "var(--text)" }}>
            ORA Self-Repair Log
          </h1>
          <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "4px 0 0" }}>
            Bugs ORA found in its OWN UI/replies, not the user's website.
            Sorted by recurrence, so "this keeps happening" bubbles to the top.
            The ledger itself never changes — only the fix-status/review below does.
          </p>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            onClick={() => setType("")}
            data-testid="admin-self-bugs-filter-all"
            style={{
              padding: "5px 12px", fontSize: 11,
              background: type === "" ? "var(--accent-2)" : "var(--panel-2)",
              color: type === "" ? "#000" : "var(--text-dim)",
              border: "1px solid var(--border)", borderRadius: 4,
              cursor: "pointer", textTransform: "uppercase", letterSpacing: 0.6,
            }}>
            All
          </button>
          {types.map((t) => (
            <button
              key={t}
              onClick={() => setType(t)}
              data-testid={`admin-self-bugs-filter-${t}`}
              style={{
                padding: "5px 12px", fontSize: 11,
                background: type === t ? "var(--accent-2)" : "var(--panel-2)",
                color: type === t ? "#000" : "var(--text-dim)",
                border: "1px solid var(--border)", borderRadius: 4,
                cursor: "pointer", textTransform: "uppercase", letterSpacing: 0.6,
              }}>
              {t.replace(/_/g, " ")}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div style={{ color: "var(--text-faint)", fontSize: 13, padding: "24px 0" }}>
          Loading self-repair log…
        </div>
      )}

      {!loading && rows.length === 0 && (
        <div data-testid="admin-self-bugs-empty"
             style={{ color: "var(--text-faint)", fontSize: 13, padding: "24px 0" }}>
          No self-bugs logged yet.
        </div>
      )}

      {!loading && rows.length > 0 && (
        <table data-testid="admin-self-bugs-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--text-faint)", borderBottom: "1px solid var(--border)" }}>
              <th style={{ padding: "6px 8px" }}>Type</th>
              <th style={{ padding: "6px 8px" }}>What the user saw</th>
              <th style={{ padding: "6px 8px" }}>Likely cause</th>
              <th style={{ padding: "6px 8px" }}>Severity</th>
              <th style={{ padding: "6px 8px" }}>Times seen</th>
              <th style={{ padding: "6px 8px" }}>First seen</th>
              <th style={{ padding: "6px 8px" }}>Last seen</th>
              <th style={{ padding: "6px 8px" }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const sig = r.signature;
              const fixed = r.status === "fixed";
              const hasProposal = !!r.proposed_fix_text;
              const pending = r.proposed_fix_review_status === "pending";
              return (
                <React.Fragment key={i}>
                  <tr data-testid={`admin-self-bugs-row-${i}`}
                      style={{ borderBottom: hasProposal ? "none" : "1px solid var(--border)" }}>
                    <td style={{ padding: "8px" }}>{r.type}</td>
                    <td style={{ padding: "8px", color: "var(--text-dim)" }}>{r.what_user_saw}</td>
                    <td style={{ padding: "8px", color: "var(--text-dim)" }}>{r.likely_cause}</td>
                    <td style={{ padding: "8px" }}>
                      <span style={{ color: SEVERITY_COLOR[r.severity] || "var(--text-dim)" }}>
                        {r.severity}
                      </span>
                    </td>
                    <td style={{ padding: "8px", fontWeight: r.times_seen > 1 ? 700 : 400 }}
                        data-testid={`admin-self-bugs-times-seen-${i}`}>
                      {r.times_seen}
                    </td>
                    <td style={{ padding: "8px", color: "var(--text-faint)" }}
                        data-testid={`admin-self-bugs-first-seen-${i}`}>
                      {fmtWhen(r.first_seen)}
                    </td>
                    <td style={{ padding: "8px", color: "var(--text-faint)" }}>
                      {fmtWhen(r.last_seen)}
                    </td>
                    <td style={{ padding: "8px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                        <span
                          data-testid={`admin-self-bugs-status-${i}`}
                          style={{
                            fontSize: 10, padding: "2px 8px", borderRadius: 4,
                            border: `1px solid ${fixed ? "var(--ok)" : "var(--border)"}`,
                            color: fixed ? "var(--ok)" : "var(--text-faint)",
                          }}>
                          {fixed ? `fixed on ${fmtWhen(r.fixed_on).split(",")[0]}` : "open"}
                        </span>
                        <button
                          data-testid={`admin-self-bugs-mark-${fixed ? "open" : "fixed"}-${i}`}
                          className="btn btn-ghost" disabled={busySig === sig}
                          style={{ fontSize: 10, padding: "2px 8px" }}
                          onClick={() => markFixed(sig, fixed ? "open" : "fixed")}>
                          {fixed ? "Re-open" : "Mark fixed"}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {hasProposal && (
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      <td colSpan={8} style={{ padding: "0 8px 10px" }}>
                        <div data-testid={`admin-self-bugs-proposal-${i}`}
                             style={{
                               fontSize: 11, padding: "8px 10px", borderRadius: 6,
                               background: "rgba(234,179,8,0.06)",
                               border: "1px solid rgba(234,179,8,0.25)",
                             }}>
                          <b style={{ color: "#eab308" }}>Proposed fix (review only, not applied)</b>
                          <div style={{ color: "var(--text-dim)", marginTop: 4 }}>{r.proposed_fix_text}</div>
                          <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8 }}>
                            <span style={{ color: "var(--text-faint)" }}>
                              status: {r.proposed_fix_review_status || "pending"}
                            </span>
                            {pending && (
                              <>
                                <button
                                  data-testid={`admin-self-bugs-approve-proposal-${i}`}
                                  className="btn btn-ghost" disabled={busySig === sig}
                                  style={{ fontSize: 10, padding: "2px 8px" }}
                                  onClick={() => reviewProposal(sig, "approved")}>
                                  Approve
                                </button>
                                <button
                                  data-testid={`admin-self-bugs-reject-proposal-${i}`}
                                  className="btn btn-ghost" disabled={busySig === sig}
                                  style={{ fontSize: 10, padding: "2px 8px" }}
                                  onClick={() => reviewProposal(sig, "rejected")}>
                                  Reject
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
