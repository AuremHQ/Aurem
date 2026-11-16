/**
 * AdminDotSignups.jsx — ORA dot signup list, admin view, 2026-11-14.
 *
 * The list Teji asked for at /admin: every name + email + join date
 * from the /dot page form (Mongo `dot_signups`, via
 * GET /api/aurem-dev/admin/dot/signups), newest first, with a one-click
 * CSV export (GET /api/aurem-dev/admin/dot/signups/export).
 *
 * Same shape as AdminSuggestions.jsx: authenticated `api.get` with the
 * Bearer token, loading / error / empty states, data-testids throughout.
 * Rendered inside the Admin shell via Admin.jsx's renderPage() switch
 * (case "dot_signups") — the shell itself is the admin gate; the
 * backend endpoint is admin-gated too (routers/admin_dot.py).
 */
import React, { useEffect, useState } from "react";
import { api, getToken } from "../lib/api";

function formatJoinedAt(joinedAt) {
  if (!joinedAt) return "—";
  const d = new Date(joinedAt * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export default function AdminDotSignups() {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    api.get("/aurem-dev/admin/dot/signups", {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then((r) => {
        setRows(r.data?.signups || []);
        setTotal(r.data?.total ?? (r.data?.signups || []).length);
      })
      .catch((err) => {
        setError(err?.response?.data?.detail || "Could not load the ORA dot list.");
      })
      .finally(() => setLoading(false));
  }, []);

  async function exportCsv() {
    setExporting(true);
    try {
      const r = await api.get("/aurem-dev/admin/dot/signups/export", {
        headers: { Authorization: `Bearer ${getToken()}` },
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(new Blob([r.data], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `ora-dot-signups-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setError("CSV export failed. Please try again.");
    } finally {
      setExporting(false);
    }
  }

  if (loading) {
    return <div data-testid="admin-dot-signups-loading">Loading ORA dot signups…</div>;
  }

  return (
    <div data-testid="admin-dot-signups">
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>ORA dot signups</h2>
        <span data-testid="admin-dot-signups-count" style={{ color: "var(--text-dim)" }}>
          {total} {total === 1 ? "person" : "people"} on the list
        </span>
        <button
          type="button"
          onClick={exportCsv}
          disabled={exporting || rows.length === 0}
          data-testid="admin-dot-signups-export"
          style={{ marginLeft: "auto", padding: "6px 14px", cursor: "pointer" }}
        >
          {exporting ? "Exporting…" : "Download CSV"}
        </button>
      </div>
      <p style={{ color: "var(--text-dim)", fontSize: 13 }}>
        Names + emails from the /dot page form. Newest first. This list is only for
        telling people when the ORA dot download is ready.
      </p>
      {error && (
        <p data-testid="admin-dot-signups-error" style={{ color: "#f87171" }}>{error}</p>
      )}
      {rows.length === 0 ? (
        <p data-testid="admin-dot-signups-empty">Nobody has joined yet.</p>
      ) : (
        <table data-testid="admin-dot-signups-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
              <th style={{ padding: "8px 10px" }}>Name</th>
              <th style={{ padding: "8px 10px" }}>Email</th>
              <th style={{ padding: "8px 10px" }}>Signed up</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={`${row.email}-${i}`} data-testid={`admin-dot-signups-row-${i}`} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "8px 10px" }}>{row.name}</td>
                <td style={{ padding: "8px 10px" }}>{row.email}</td>
                <td style={{ padding: "8px 10px" }}>{formatJoinedAt(row.joined_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
