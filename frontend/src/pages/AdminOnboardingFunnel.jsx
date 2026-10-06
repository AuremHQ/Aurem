/**
 * AdminOnboardingFunnel.jsx — "why is this user stuck?" dashboard (2026-09-08)
 *
 * Founder ask: make the onboarding funnel accurate + trackable so we
 * can tell WHY a user is stuck, without a production DB curl. Reuses
 * the already-correct stage classification from
 * services/funnel_nudge_cron.classify_users() (same logic that decides
 * who gets a nudge email) via GET /admin/onboarding-funnel/summary and
 * GET /admin/onboarding-funnel/users?stage=X.
 *
 * Row → "View →" reuses the EXISTING drill-down pattern from
 * AdminOverview's Live Cost Alert card: navigate to
 * /admin/users?drill_user=<id>, which Admin.jsx already wires straight
 * into UserDetail (activity timeline, projects, emails sent, etc.).
 *
 * 2026-09-08 round 2 — founder asked for 3 more things on this same
 * page, explicitly PREVIEW/EXPORT only (no auto-send):
 *   - Nudge email PREVIEW per stage (read-only, no real token minted)
 *   - CSV export of a stage's stuck-user list (client-side, no new
 *     backend endpoint needed — the full list is already in memory)
 *   - Funnel TREND line (daily snapshot history via
 *     GET /admin/onboarding-funnel/trend, recharts LineChart)
 */
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { Loader2, RefreshCw, ArrowRight, Mail, Download, X } from "lucide-react";
import { api } from "../lib/api";
import { Card, Table, Badge, ago } from "./Admin";

const STAGE_ACCENT = {
  stage4_fully_inactive:  "var(--text-faint)",
  stage1_github_started:  "#eab308",
  stage2_project_pending: "var(--accent-2)",
  stage3_no_chat:         "var(--danger)",
};

// `detail` can be a list/object (FastAPI validation errors); only a string is
// safe to render.
function errorText(e) {
  const d = e?.response?.data?.detail;
  return typeof d === "string" ? d : (e?.message || "request failed");
}

function toCsv(rows) {
  const cols = ["email", "name", "stuck_since", "stuck_days", "nudge_sent", "nudge_sent_at", "nudge_clicked"];
  const escape = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.join(",")];
  for (const r of rows) lines.push(cols.map((c) => escape(r[c])).join(","));
  return lines.join("\n");
}

function downloadCsv(filename, csvText) {
  const blob = new Blob([csvText], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function AdminOnboardingFunnel() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeStage, setActiveStage] = useState(null);
  const [users, setUsers] = useState(null);
  const [usersLoading, setUsersLoading] = useState(false);
  const [trend, setTrend] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [trendFailed, setTrendFailed] = useState(false);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/onboarding-funnel/summary");
      setSummary(r.data);
      setLoadError(null);
    } catch (e) {
      setLoadError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTrend = useCallback(async () => {
    try {
      const r = await api.get("/admin/onboarding-funnel/trend", { params: { days: 30 } });
      setTrend(r.data);
      setTrendFailed(false);
    } catch {
      setTrendFailed(true);
    }
  }, []);

  useEffect(() => { loadSummary(); loadTrend(); }, [loadSummary, loadTrend]);

  const openStage = useCallback(async (stageKey) => {
    setActiveStage(stageKey);
    setPreview(null);
    setUsersLoading(true);
    try {
      const r = await api.get("/admin/onboarding-funnel/users", { params: { stage: stageKey } });
      setUsers(r.data);
    } catch (e) {
      setLoadError(errorText(e));
    } finally {
      setUsersLoading(false);
    }
  }, []);

  const openPreview = useCallback(async (stageKey) => {
    setPreviewLoading(true);
    try {
      const r = await api.get("/admin/onboarding-funnel/preview-email", { params: { stage: stageKey } });
      setPreview(r.data);
    } catch (e) {
      setLoadError(errorText(e));
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  const exportCsv = useCallback(() => {
    if (!users?.users?.length) return;
    downloadCsv(`stuck-users-${activeStage}-${new Date().toISOString().slice(0, 10)}.csv`, toCsv(users.users));
  }, [users, activeStage]);

  const trendSeries = (trend?.points || []).map((p) => ({
    date: p.date?.slice(5), // MM-DD
    "Signed up, never started": p.stages?.stage4_fully_inactive ?? 0,
    "GitHub started": p.stages?.stage1_github_started ?? 0,
    "Project pending": p.stages?.stage2_project_pending ?? 0,
    "Connected, no task": p.stages?.stage3_no_chat ?? 0,
  }));

  return (
    <div data-testid="admin-onboarding-funnel-page" style={{ padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", marginBottom: 4, gap: 12, flexWrap: "wrap" }}>
        <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                      color: "var(--text-faint)", margin: 0 }}>
          Onboarding Funnel — why users are stuck
        </h3>
        <button data-testid="admin-onboarding-funnel-refresh"
                className="btn-ghost" style={{ fontSize: 11, padding: "5px 12px" }}
                onClick={() => { loadSummary(); loadTrend(); }} disabled={loading}>
          <RefreshCw size={12} style={{ verticalAlign: "middle", marginRight: 6 }} />
          Refresh
        </button>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 18 }}>
        Every signed-up user stuck 24h+ at their most-advanced funnel step, live-classified
        from real DB state (same logic the nudge-email cron uses). Click a stage to see who.
      </div>

      {loadError && (
        <div data-testid="admin-onboarding-funnel-error"
             style={{ padding: 12, marginBottom: 14, fontSize: 12, color: "var(--danger)",
                      border: "1px solid var(--border)", borderRadius: 8 }}>
          Couldn't load the funnel: {loadError}{" "}
          <button className="btn-ghost" style={{ fontSize: 11, padding: "3px 10px" }}
                  data-testid="admin-onboarding-funnel-retry"
                  onClick={() => { loadSummary(); loadTrend(); }}>
            Retry
          </button>
        </div>
      )}

      {loading && !summary ? (
        <div style={{ padding: 24, color: "var(--text-faint)" }}>
          <Loader2 size={14} className="spin" /> Loading…
        </div>
      ) : (
        <>
          <div data-testid="admin-onboarding-funnel-stage-cards"
               style={{ display: "grid",
                        gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                        gap: 12, marginBottom: 20 }}>
            {(summary?.stages || []).map((s) => {
              const active = activeStage === s.key;
              const accent = STAGE_ACCENT[s.key] || "var(--text)";
              return (
                <Card
                  key={s.key}
                  style={{
                    padding: 16,
                    border: active ? `1px solid ${accent}` : "1px solid var(--border)",
                  }}
                >
                  <div data-testid={`admin-onboarding-stage-${s.key}`}
                       style={{ cursor: "pointer" }} onClick={() => openStage(s.key)}>
                    <div style={{ fontSize: 10, letterSpacing: "0.08em",
                                   color: "var(--text-faint)", textTransform: "uppercase" }}>
                      {s.label}
                    </div>
                    <div style={{ fontSize: 28, fontWeight: 600, marginTop: 6,
                                   color: accent,
                                   fontFamily: "'JetBrains Mono', monospace" }}>
                      {s.count}
                    </div>
                    <div style={{ fontSize: 11, marginTop: 6, color: "var(--text-dim)",
                                   lineHeight: 1.4 }}>
                      {s.why}
                    </div>
                    {s.oldest_stuck_days != null && (
                      <div style={{ fontSize: 11, marginTop: 8, color: "var(--text-faint)" }}>
                        Stuck {s.median_stuck_days}d (median) · oldest {s.oldest_stuck_days}d
                      </div>
                    )}
                  </div>
                  <button
                    data-testid={`admin-onboarding-preview-email-${s.key}`}
                    className="btn-ghost"
                    style={{ fontSize: 10, padding: "4px 8px", marginTop: 10 }}
                    onClick={(e) => { e.stopPropagation(); openPreview(s.key); }}
                  >
                    <Mail size={10} style={{ verticalAlign: "middle", marginRight: 4 }} />
                    Preview nudge email
                  </button>
                </Card>
              );
            })}
          </div>

          {summary && (
            <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 16 }}>
              Total stuck (24h+): <b style={{ color: "var(--text)" }}>{summary.total_stuck}</b>
              {" · "}generated {ago(summary.generated_at)}
            </div>
          )}

          {preview && (
            <Card data-testid="admin-onboarding-preview-panel"
                  style={{ marginBottom: 20, border: "1px solid var(--accent-2)" }}>
              <div style={{ display: "flex", justifyContent: "space-between",
                             alignItems: "flex-start", marginBottom: 8 }}>
                <div>
                  <Badge color="var(--accent-2)">PREVIEW ONLY — not sent</Badge>
                  <div style={{ fontSize: 13, fontWeight: 600, marginTop: 8 }}>
                    {preview.subject}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 2 }}>
                    {preview.is_placeholder
                      ? "No real user currently in this stage — showing a placeholder sample."
                      : `Rendered for the most-stuck real user: ${preview.sample_email}`}
                  </div>
                </div>
                <button data-testid="admin-onboarding-preview-close"
                        onClick={() => setPreview(null)}
                        style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-faint)" }}>
                  <X size={14} />
                </button>
              </div>
              {previewLoading ? (
                <div style={{ color: "var(--text-faint)", fontSize: 12 }}><Loader2 size={12} className="spin" /> Loading…</div>
              ) : (
                <pre data-testid="admin-onboarding-preview-body" style={{
                  fontSize: 12, color: "var(--text-dim)", whiteSpace: "pre-wrap",
                  background: "var(--bg-elev)", borderRadius: 6, padding: 12, margin: 0,
                }}>{preview.text}</pre>
              )}
            </Card>
          )}

          <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                        color: "var(--text-faint)", margin: "0 0 8px" }}>
            Funnel trend — last 30 days
          </h3>
          <Card style={{ marginBottom: 20 }}>
            {trendFailed && !trend ? (
              <div data-testid="admin-onboarding-trend-error" style={{ padding: 12, fontSize: 12, color: "var(--text-faint)" }}>
                Couldn't load the trend. Use Refresh to try again.
              </div>
            ) : !trend ? (
              <div style={{ padding: 24, color: "var(--text-faint)" }}><Loader2 size={14} className="spin" /> Loading…</div>
            ) : (trend.points?.length ?? 0) < 2 ? (
              <div data-testid="admin-onboarding-trend-empty" style={{ padding: 12, fontSize: 12, color: "var(--text-faint)" }}>
                Only {trend.points?.length ?? 0} day of data so far — the daily snapshot cron
                just started. The trend line fills in day by day; check back tomorrow.
              </div>
            ) : (
              <div data-testid="admin-onboarding-trend-chart">
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={trendSeries} margin={{ top: 4, right: 10, left: -10, bottom: 0 }}>
                    <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                    <XAxis dataKey="date" stroke="var(--text-faint)" fontSize={10} />
                    <YAxis stroke="var(--text-faint)" fontSize={10} allowDecimals={false} />
                    <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", fontSize: 11, borderRadius: 6 }} />
                    <Line type="monotone" dataKey="Signed up, never started" stroke={STAGE_ACCENT.stage4_fully_inactive} strokeWidth={2} dot={{ r: 2 }} />
                    <Line type="monotone" dataKey="GitHub started" stroke={STAGE_ACCENT.stage1_github_started} strokeWidth={2} dot={{ r: 2 }} />
                    <Line type="monotone" dataKey="Project pending" stroke={STAGE_ACCENT.stage2_project_pending} strokeWidth={2} dot={{ r: 2 }} />
                    <Line type="monotone" dataKey="Connected, no task" stroke={STAGE_ACCENT.stage3_no_chat} strokeWidth={2} dot={{ r: 2 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </Card>

          {activeStage && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                              color: "var(--text-faint)", margin: 0 }}>
                  {users?.label || activeStage} ({users?.count ?? "…"})
                </h3>
                <button data-testid="admin-onboarding-funnel-export-csv"
                        className="btn-ghost" style={{ fontSize: 11, padding: "5px 12px" }}
                        onClick={exportCsv} disabled={!users?.users?.length}>
                  <Download size={12} style={{ verticalAlign: "middle", marginRight: 6 }} />
                  Export CSV
                </button>
              </div>
              <Card>
                {usersLoading ? (
                  <div style={{ padding: 24, color: "var(--text-faint)" }}>
                    <Loader2 size={14} className="spin" /> Loading…
                  </div>
                ) : (
                  <div data-testid="admin-onboarding-funnel-user-table">
                    <Table
                      cols={["Email", "Name", "Stuck since", "Nudge email", ""]}
                      rows={(users?.users || []).map((u) => [
                        u.email || "—",
                        u.name || "—",
                        <span key="stuck" style={{ color: "var(--text-faint)" }}>
                          {u.stuck_days != null ? `${u.stuck_days}d ago` : "—"}
                        </span>,
                        u.nudge_sent
                          ? <Badge key="nudge" color={u.nudge_clicked ? "var(--ok)" : "#eab308"}>
                              {u.nudge_clicked ? "sent · clicked" : "sent · not clicked"}
                            </Badge>
                          : <Badge key="nudge">not sent yet</Badge>,
                        <button
                          key="view"
                          data-testid={`admin-onboarding-funnel-view-${u.user_id}`}
                          className="btn-ghost"
                          style={{ padding: "4px 10px", fontSize: 11 }}
                          onClick={() => navigate(`/admin/users?drill_user=${encodeURIComponent(u.user_id)}`)}
                        >
                          view <ArrowRight size={11} style={{ verticalAlign: "middle" }} />
                        </button>,
                      ])}
                    />
                  </div>
                )}
              </Card>
            </>
          )}
        </>
      )}
    </div>
  );
}
