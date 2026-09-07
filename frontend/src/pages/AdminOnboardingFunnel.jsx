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
 */
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, RefreshCw, ArrowRight } from "lucide-react";
import { api } from "../lib/api";
import { Card, Table, Badge, ago } from "./Admin";

const STAGE_ACCENT = {
  stage4_fully_inactive:  "var(--text-faint)",
  stage1_github_started:  "#eab308",
  stage2_project_pending: "var(--accent-2)",
  stage3_no_chat:         "var(--danger)",
};

export default function AdminOnboardingFunnel() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeStage, setActiveStage] = useState(null);
  const [users, setUsers] = useState(null);
  const [usersLoading, setUsersLoading] = useState(false);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/onboarding-funnel/summary");
      setSummary(r.data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSummary(); }, [loadSummary]);

  const openStage = useCallback(async (stageKey) => {
    setActiveStage(stageKey);
    setUsersLoading(true);
    try {
      const r = await api.get("/admin/onboarding-funnel/users", { params: { stage: stageKey } });
      setUsers(r.data);
    } finally {
      setUsersLoading(false);
    }
  }, []);

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
                onClick={loadSummary} disabled={loading}>
          <RefreshCw size={12} style={{ verticalAlign: "middle", marginRight: 6 }} />
          Refresh
        </button>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 18 }}>
        Every signed-up user stuck 24h+ at their most-advanced funnel step, live-classified
        from real DB state (same logic the nudge-email cron uses). Click a stage to see who.
      </div>

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
                    padding: 16, cursor: "pointer",
                    border: active ? `1px solid ${accent}` : "1px solid var(--border)",
                  }}
                >
                  <div
                    data-testid={`admin-onboarding-stage-${s.key}`}
                    onClick={() => openStage(s.key)}
                  >
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

          {activeStage && (
            <>
              <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                            color: "var(--text-faint)", margin: "0 0 8px" }}>
                {users?.label || activeStage} ({users?.count ?? "…"})
              </h3>
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
