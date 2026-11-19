/**
 * AdminHarnessOverview.jsx — 2026-11-16.
 * /admin/harness — the admin side-panel "Harness" section.
 *
 * Four real-data cards, nothing hardcoded or invented:
 *   - Signups: GET /admin/harness/signups (email/date/signup_source),
 *     with a toggle to filter to harness_homepage-attributed rows.
 *   - Downloads: reuses the EXISTING GET /admin/harness/downloads
 *     (routers/admin_harness.py) — not duplicated here.
 *   - Releases: links to the EXISTING /admin/harness-releases page —
 *     not duplicated here either.
 *   - Telemetry: GET /admin/harness/telemetry-status (event count +
 *     last received timestamp).
 */
import React, { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Users, Download, Package, Radio, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import { cleanErr } from "../lib/cleanErr";
import { toast } from "../components/Toast";

function Card({ children, style }) {
  return (
    <div style={{ background: "#0f0f0f", border: "1px solid #262626", borderRadius: 12, padding: 20, ...style }}>
      {children}
    </div>
  );
}

function fmtDate(seconds) {
  if (!seconds) return "—";
  const d = new Date(seconds * 1000);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}


export default function AdminHarnessOverview() {
  const [signups, setSignups] = useState([]);
  const [harnessOnly, setHarnessOnly] = useState(false);
  const [harnessCount, setHarnessCount] = useState(0);
  const [downloads, setDownloads] = useState({ downloads: [], total: 0, free_count: 0, paid_count: 0 });
  const [telemetry, setTelemetry] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (filterHarness) => {
    setLoading(true);
    try {
      const [signupsRes, downloadsRes, telemetryRes] = await Promise.all([
        api.get("/admin/harness/signups", filterHarness ? { params: { source: "harness_homepage" } } : undefined),
        api.get("/admin/harness/downloads"),
        api.get("/admin/harness/telemetry-status"),
      ]);
      setSignups(signupsRes.data?.signups || []);
      setHarnessCount(signupsRes.data?.harness_homepage_count || 0);
      setDownloads(downloadsRes.data || { downloads: [], total: 0, free_count: 0, paid_count: 0 });
      setTelemetry(telemetryRes.data || null);
    } catch (e) {
      toast({ message: cleanErr(e, "Failed to load harness overview"), kind: "error" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(harnessOnly); }, [load, harnessOnly]);

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#e5e5e5", fontFamily: "system-ui, -apple-system, sans-serif", padding: "28px 24px 60px" }}>
      <div style={{ maxWidth: 1000, margin: "0 auto" }}>
        <Link to="/admin/cockpit" data-testid="harness-overview-back-link"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#888", fontSize: 12, textDecoration: "none", marginBottom: 18 }}>
          <ArrowLeft size={12} /> Cockpit
        </Link>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, display: "flex", alignItems: "center", gap: 10, margin: 0 }}>
            <Radio size={18} color="#ff8a2a" /> Harness
          </h1>
          <button data-testid="harness-overview-refresh-btn" onClick={() => load(harnessOnly)} disabled={loading}
                  style={{ padding: "6px 12px", fontSize: 12, borderRadius: 6, border: "1px solid #333", background: "#181818", color: "#ccc", cursor: loading ? "wait" : "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}>
            <RefreshCw size={12} /> Refresh
          </button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 16, marginBottom: 16 }}>
          <Card>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <Users size={15} color="#ff8a2a" />
              <strong style={{ fontSize: 13 }}>Signups</strong>
            </div>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#aaa", marginBottom: 12, cursor: "pointer" }}>
              <input type="checkbox" data-testid="harness-overview-signups-filter"
                     checked={harnessOnly} onChange={(e) => setHarnessOnly(e.target.checked)} />
              harness_homepage only ({harnessCount})
            </label>
            <div data-testid="harness-overview-signups-list" style={{ maxHeight: 260, overflowY: "auto" }}>
              {signups.length === 0 ? (
                <div style={{ fontSize: 12, color: "#666" }}>No signups yet.</div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
                  <thead>
                    <tr>
                      {["Email", "Date", "Source"].map((h) => (
                        <th key={h} style={{ textAlign: "left", padding: "4px 6px", color: "#666", textTransform: "uppercase", fontSize: 10, borderBottom: "1px solid #262626" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {signups.map((s, i) => (
                      <tr key={s.email || i} data-testid={`harness-overview-signup-row-${i}`}>
                        <td style={{ padding: "4px 6px", borderBottom: "1px solid #1a1a1a" }}>{s.email}</td>
                        <td style={{ padding: "4px 6px", borderBottom: "1px solid #1a1a1a", color: "#999" }}>{fmtDate(s.created_at)}</td>
                        <td style={{ padding: "4px 6px", borderBottom: "1px solid #1a1a1a" }}>
                          {s.signup_source
                            ? <span style={{ color: "#3ECF8E" }}>{s.signup_source}</span>
                            : <span style={{ color: "#666" }}>—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </Card>

          <Card>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <Download size={15} color="#ff8a2a" />
              <strong style={{ fontSize: 13 }}>Downloads</strong>
            </div>
            <div data-testid="harness-overview-downloads-total" style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>
              {downloads.total}
            </div>
            <div style={{ fontSize: 11.5, color: "#999", marginBottom: 12 }}>
              {downloads.free_count} free · {downloads.paid_count} paid
            </div>
            <div data-testid="harness-overview-downloads-recent" style={{ maxHeight: 180, overflowY: "auto", fontSize: 11.5 }}>
              {(downloads.downloads || []).slice(0, 10).map((d, i) => (
                <div key={d.grant_id || i} style={{ padding: "4px 0", borderBottom: "1px solid #1a1a1a", color: "#999" }}>
                  v{d.version} · {d.plan} · {fmtDate(d.issued_at)}
                </div>
              ))}
              {downloads.total === 0 && <div style={{ color: "#666" }}>No downloads yet.</div>}
            </div>
          </Card>

          <Card>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <Package size={15} color="#ff8a2a" />
              <strong style={{ fontSize: 13 }}>Releases</strong>
            </div>
            <p style={{ fontSize: 12, color: "#999", marginBottom: 12 }}>
              Release creation and publishing live on their own page.
            </p>
            <Link to="/admin/harness-releases" data-testid="harness-overview-releases-link"
                  style={{ display: "inline-block", padding: "6px 14px", fontSize: 12, fontWeight: 600, borderRadius: 6, border: "1px solid #ff8a2a", color: "#ff8a2a", textDecoration: "none" }}>
              Open Loop Releases →
            </Link>
          </Card>

          <Card>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <Radio size={15} color="#ff8a2a" />
              <strong style={{ fontSize: 13 }}>Telemetry</strong>
            </div>
            {telemetry ? (
              <div data-testid="harness-overview-telemetry-status" style={{ fontSize: 12.5 }}>
                <div style={{ color: "#999", marginBottom: 6 }}>
                  Endpoint: <span style={{ fontFamily: "monospace", color: "#e5e5e5" }}>{telemetry.endpoint}</span>
                </div>
                <div style={{ color: "#999", marginBottom: 6 }}>
                  Events received: <strong style={{ color: "#e5e5e5" }}>{telemetry.total_events}</strong>
                </div>
                <div style={{ color: "#999" }}>
                  Last event: {telemetry.last_received_at ? fmtDate(new Date(telemetry.last_received_at).getTime() / 1000) : "never"}
                </div>
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "#666" }}>Loading…</div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
