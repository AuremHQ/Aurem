/**
 * AdminFunnelPage.jsx — 2026-09-28 MASTER TASK "ACTIVATION FIRST" Part 1
 *
 * Permanent, admin-only, READ-ONLY activation-funnel dashboard:
 *   signed up -> opened chat -> connected repo OR skipped ->
 *   sent first message -> first task started -> first task completed ->
 *   came back another day
 * plus WHY users drop between "sent first message" and "first task
 * completed" (provable reasons only — "unknown" if it can't be proven
 * from logs), and first-task token cost (median/p90, with vs without
 * a connected repo).
 *
 * Reads GET /admin/funnel/activation?start=&end= (date-range filter).
 * Deliberately NOT at /admin/funnel's existing API path — that one
 * already powers AdminOnboardingFunnel.jsx's `nudge_stages` cron
 * classification; this is a separate, more granular report living at
 * its own URL so the older consumer is never disturbed.
 */
import { useCallback, useEffect, useState } from "react";
import { RefreshCw, TrendingDown, Coins } from "lucide-react";
import { api } from "../lib/api";
import { Card, MCard, Table, Badge } from "./Admin";

const REASON_LABEL = {
  scan_failed: "Website scan failed",
  llm_error_or_timeout: "LLM error or timeout",
  monthly_task_limit_hit: "Monthly task limit hit",
  no_repo_connected: "No repo connected",
  low_confidence_or_refused: "ORA couldn't help / refused",
  unknown: "Unknown (not provable from logs)",
};

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function FunnelBar({ step, prevCount }) {
  const widthPct = Math.max(2, step.pct);
  const dropFromPrev = prevCount != null ? prevCount - step.count : null;
  return (
    <div data-testid={`funnel-step-${step.step}`} style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     fontSize: 12, color: "var(--text-dim)", marginBottom: 3 }}>
        <span>{step.label}</span>
        <span>
          <b style={{ color: "var(--text)" }}>{step.count}</b> ({step.pct}%)
          {dropFromPrev != null && dropFromPrev > 0 && (
            <span style={{ color: "var(--danger)", marginLeft: 8 }}>
              −{dropFromPrev}
            </span>
          )}
        </span>
      </div>
      <div style={{ height: 10, background: "var(--bg-elev)", borderRadius: 5, overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${widthPct}%`,
          background: "linear-gradient(90deg, var(--accent), var(--accent-2, var(--accent)))",
          borderRadius: 5, transition: "width 0.4s ease",
        }} />
      </div>
    </div>
  );
}

export default function AdminFunnelPage() {
  const today = new Date();
  const monthAgo = new Date(today.getTime() - 30 * 86400000);
  const [start, setStart] = useState(isoDate(monthAgo));
  const [end, setEnd] = useState(isoDate(today));
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await api.get("/admin/funnel/activation", { params: { start, end }, timeout: 20000 });
      if (!r.data?.funnel || !r.data.first_task_tokens || !r.data.drop_reasons) {
        throw new Error("Unexpected response (missing funnel, first_task_tokens or drop_reasons)");
      }
      setData(r.data);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "failed to load");
    } finally {
      setLoading(false);
    }
  }, [start, end]);

  useEffect(() => { load(); }, [load]);

  return (
    <div data-testid="admin-funnel-page" style={{ padding: 20, maxWidth: 980 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", marginBottom: 4 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, color: "var(--text)", margin: 0 }}>
          Activation Funnel
        </h2>
        <button
          data-testid="admin-funnel-refresh-btn"
          onClick={load}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            background: "var(--bg-elev)", border: "1px solid var(--border)",
            borderRadius: 6, padding: "6px 12px", color: "var(--text-dim)",
            fontSize: 12, cursor: "pointer",
          }}
        >
          <RefreshCw size={13} className={loading ? "spin" : ""} /> Refresh
        </button>
      </div>
      <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "4px 0 16px" }}>
        Read-only. Computed live from real event data — signed up →
        …→ came back another day. No writes, no bulk email export.
      </p>

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 16 }}>
        <label style={{ fontSize: 11, color: "var(--text-faint)" }}>
          From{" "}
          <input data-testid="admin-funnel-start-date" type="date" value={start}
                 onChange={(e) => setStart(e.target.value)}
                 style={{ background: "var(--bg-elev)", border: "1px solid var(--border)",
                          color: "var(--text)", borderRadius: 4, padding: "3px 6px", marginLeft: 4 }} />
        </label>
        <label style={{ fontSize: 11, color: "var(--text-faint)" }}>
          To{" "}
          <input data-testid="admin-funnel-end-date" type="date" value={end}
                 onChange={(e) => setEnd(e.target.value)}
                 style={{ background: "var(--bg-elev)", border: "1px solid var(--border)",
                          color: "var(--text)", borderRadius: 4, padding: "3px 6px", marginLeft: 4 }} />
        </label>
      </div>

      {err && (
        <div data-testid="admin-funnel-error" style={{
          color: "var(--danger)", fontSize: 12, marginBottom: 16,
          padding: "8px 12px", border: "1px solid var(--border)", borderRadius: 6,
        }}>
          {err}
        </div>
      )}

      {loading && !data && (
        <div style={{ color: "var(--text-faint)", fontSize: 12 }}>Loading…</div>
      )}

      {data && (
        <>
          <Card style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 12 }}>
              {data.cohort_size} non-test signups between {data.range?.start} and {data.range?.end}
            </div>
            {data.note && (
              <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 10 }}>{data.note}</div>
            )}
            {(data.funnel || []).map((step, i) => (
              <FunnelBar key={step.step} step={step}
                         prevCount={i > 0 ? data.funnel[i - 1].count : null} />
            ))}
          </Card>

          <Card style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <TrendingDown size={15} color="var(--danger)" />
              <b style={{ fontSize: 13, color: "var(--text)" }}>
                Dropped between "sent first message" and "first task completed"
              </b>
              <Badge>{data.drop_reasons?.cohort ?? 0} users</Badge>
            </div>
            <Table
              cols={["Reason", "Users"]}
              rows={(data.drop_reasons?.reasons || []).map((r) => [
                REASON_LABEL[r.reason] || r.reason,
                r.count,
              ])}
            />
          </Card>

          <Card style={{ padding: 18 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <Coins size={15} color="var(--accent)" />
              <b style={{ fontSize: 13, color: "var(--text)" }}>
                First completed task — token cost
              </b>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <MCard
                label="With repo connected"
                value={data.first_task_tokens.with_repo
                  ? `${data.first_task_tokens.with_repo.median.toLocaleString()} tok`
                  : "no data"}
                sub={data.first_task_tokens.with_repo
                  ? `p90 ${data.first_task_tokens.with_repo.p90.toLocaleString()} · n=${data.first_task_tokens.with_repo.n}`
                  : "not enough completed first tasks yet"}
              />
              <MCard
                label="Without repo (e.g. website scan)"
                value={data.first_task_tokens.without_repo
                  ? `${data.first_task_tokens.without_repo.median.toLocaleString()} tok`
                  : "no data"}
                sub={data.first_task_tokens.without_repo
                  ? `p90 ${data.first_task_tokens.without_repo.p90.toLocaleString()} · n=${data.first_task_tokens.without_repo.n}`
                  : "not enough completed first tasks yet"}
              />
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
