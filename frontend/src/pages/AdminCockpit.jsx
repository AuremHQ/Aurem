/**
 * AdminCockpit.jsx — Unified cockpit page (Feb 2026)
 *
 * THIN consumer of /api/aurem-dev/admin/status/all — never computes
 * its own status. Three separate sections (per founder's 3-column
 * spec):
 *
 *   1. System Health — checks only. Donut + heartbeat + "Needs
 *                       Attention" (red-only) + "Setup Pending"
 *                       (gray-only). health_pct excludes gray.
 *   2. Mode Board   — chips: Stripe test/live, environment, etc.
 *                       Not checks. Not red/green dots.
 *   3. Business Pulse — metric cards from /admin/dashboard.
 *
 * Zero mocks. All data live. Inline-expand for red items — never
 * force a tab jump.
 */
import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import LiveBusinessIntelligence from "../components/LiveBusinessIntelligence";
import HealthScoreWidget from "../components/HealthScoreWidget";
import ChurnRiskWidget from "../components/ChurnRiskWidget";
import DuplicationWidget from "../components/DuplicationWidget";

const C = {
  bg:     "#0a0a0a",
  panel:  "#101013",
  border: "rgba(255,255,255,0.10)",
  text:   "#e5e5e5",
  faint:  "#5f5f5f",
  dim:    "#8a8a8a",
  amber:  "#f5a524",
  red:    "#ef4444",
  green:  "#22c55e",
  gray:   "#6b7280",
  mono:   "SFMono-Regular, Menlo, Consolas, monospace",
};

const POLL_MS = 30000;   // 30s cockpit poll — matches aggregator TTL

function Dot({ status, size = 8 }) {
  const color = status === "green" ? C.green
              : status === "red"   ? C.red
              : C.gray;
  return (
    <span style={{
      display: "inline-block",
      width: size, height: size,
      borderRadius: "50%",
      background: color,
      boxShadow: status === "red" ? `0 0 6px ${C.red}` : "none",
    }} />
  );
}

function useCockpitData() {
  const [payload, setPayload] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancel = false;
    const fetchNow = async () => {
      try {
        // Feb 2026 · prod-hang fix — cap the aggregator fetch at 15s
        // so a stalled backend never leaves the cockpit body spinning
        // forever.  The backend itself now enforces a 20s outer
        // timeout (defence in depth), but the UI must fail-fast on
        // its own too so the user gets a visible error state instead
        // of an indefinite skeleton.
        const r = await api.get("/admin/status/all", { timeout: 15000 });
        if (!cancel) { setPayload(r.data); setErr(null); }
      } catch (e) {
        if (!cancel) {
          const isTimeout = e?.code === "ECONNABORTED" || /timeout/i.test(e?.message || "");
          setErr(isTimeout
            ? "status/all timed out after 15s — backend aggregator is slow or a check is blocking"
            : (e?.message || "fetch failed"));
        }
      } finally {
        if (!cancel) setLoading(false);
      }
    };
    fetchNow();
    const t = setInterval(fetchNow, POLL_MS);
    return () => { cancel = true; clearInterval(t); };
  }, [refreshKey]);

  const refresh = () => setRefreshKey((k) => k + 1);
  return { payload, err, loading, refresh };
}

function HealthDonut({ counts }) {
  const g = counts?.green || 0;
  const r = counts?.red || 0;
  const denom = g + r;
  const pct = denom ? Math.round(100 * g / denom) : 0;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 20,
      padding: 18, background: C.panel, border: `1px solid ${C.border}`,
      borderRadius: 12,
    }}>
      <div style={{ position: "relative", width: 96, height: 96 }}>
        <svg viewBox="0 0 36 36" width="96" height="96">
          <circle cx="18" cy="18" r="15.9" fill="none"
            stroke="rgba(255,255,255,0.06)" strokeWidth="3" />
          <circle cx="18" cy="18" r="15.9" fill="none"
            stroke={r > 0 ? C.red : C.green} strokeWidth="3"
            strokeDasharray={`${pct}, 100`}
            transform="rotate(-90 18 18)" strokeLinecap="round" />
        </svg>
        <div style={{
          position: "absolute", inset: 0,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontFamily: C.mono, fontSize: 22, color: C.text, fontWeight: 600,
        }}>
          {denom ? `${pct}%` : "—"}
        </div>
      </div>
      <div>
        <div style={{ fontFamily: C.mono, fontSize: 10,
             letterSpacing: "0.14em", color: C.faint, marginBottom: 6 }}>
          SYSTEM HEALTH
        </div>
        <div style={{ display: "flex", gap: 14, fontSize: 12, color: C.dim }}>
          <span><Dot status="green" /> <b style={{ color: C.text }}>{counts?.green || 0}</b> passing</span>
          <span><Dot status="red" />   <b style={{ color: C.red  }}>{counts?.red   || 0}</b> failing</span>
          <span><Dot status="gray" />  <b style={{ color: C.gray }}>{counts?.gray  || 0}</b> unconfigured</span>
        </div>
        <div style={{ fontSize: 10, color: C.faint, marginTop: 8, fontFamily: C.mono }}>
          health% excludes gray · total {counts?.total || 0}
        </div>
      </div>
    </div>
  );
}

function CheckRow({ c, expanded, onToggle, onAck }) {
  const dot = c.status;
  const link = (
    c.category === "guard"       ? "/admin/qa" :
    c.category === "integration" ? "/admin/integrations" :
    "/admin/system-health"
  );
  const canAck = c.status === "red" && !c.ack_active;
  return (
    <div data-testid={`cockpit-check-${c.id}`}
      style={{ padding: "10px 12px", borderBottom: `1px solid ${C.border}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}
           onClick={onToggle}>
        <Dot status={dot} />
        <span style={{ color: C.text, fontSize: 13, fontWeight: 500 }}>{c.name}</span>
        <span style={{ marginLeft: "auto", color: C.faint, fontSize: 10, fontFamily: C.mono }}>
          {c.category}
        </span>
      </div>
      {expanded && (
        <div style={{ marginLeft: 18, marginTop: 6 }}>
          <div style={{ fontSize: 11, color: C.dim }}>{c.detail || "—"}</div>
          <div style={{ fontSize: 10, color: C.faint, fontFamily: C.mono, marginTop: 4 }}>
            checked_at: {c.checked_at}
            {c.red_since ? ` · red since ${c.red_since}` : ""}
          </div>
          <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 6 }}>
            <Link to={link}
              data-testid={`cockpit-view-full-${c.id}`}
              style={{ fontSize: 11, color: C.amber }}>
              View full →
            </Link>
            {canAck && (
              <button
                data-testid={`cockpit-ack-${c.id}`}
                onClick={(e) => { e.stopPropagation(); onAck(c.id, 24); }}
                title="Mute this check for 24 hours"
                style={{
                  fontSize: 11, background: "transparent",
                  border: `1px solid ${C.border}`, color: C.dim,
                  padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                  fontFamily: C.mono,
                }}>
                Ack 24h
              </button>
            )}
            {c.ack_active && (
              <button
                data-testid={`cockpit-unack-${c.id}`}
                onClick={(e) => { e.stopPropagation(); onAck(c.id, 0); }}
                title="Clear acknowledgement"
                style={{
                  fontSize: 11, background: "transparent",
                  border: `1px solid ${C.border}`, color: C.dim,
                  padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                  fontFamily: C.mono,
                }}>
                Un-ack
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function CheckList({ title, filterFn, checks, testid, onAck }) {
  const [expanded, setExpanded] = useState(null);
  const rows = (checks || []).filter(filterFn);
  return (
    <div data-testid={testid}
         style={{ background: C.panel, border: `1px solid ${C.border}`,
                  borderRadius: 12, marginTop: 14 }}>
      <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}`,
                    fontFamily: C.mono, fontSize: 10, letterSpacing: "0.14em",
                    color: C.faint }}>
        {title} · {rows.length}
      </div>
      {rows.length === 0 && (
        <div style={{ padding: "18px", textAlign: "center", color: C.faint, fontSize: 12 }}>
          none
        </div>
      )}
      {rows.map((c) => (
        <CheckRow key={c.id} c={c}
          expanded={expanded === c.id}
          onToggle={() => setExpanded(expanded === c.id ? null : c.id)}
          onAck={onAck} />
      ))}
    </div>
  );
}

export function ModeBoard() {
  // State (mode) surfaces — NOT health checks. Fed by real endpoints.
  const [ver, setVer] = useState(null);
  const [stripe, setStripe] = useState(null);
  useEffect(() => {
    api.get("/version").then(r => setVer(r.data)).catch(() => {});
    // 2026-09 fix: was reading the binary int_stripe health check
    // (green=configured / anything else="not-set"), which collapsed
    // "warn" (test mode, key present but charges disabled) into the
    // SAME "not-set" label as genuinely missing — showed "not-set"
    // even when Stripe was actually configured in test mode, which
    // disagreed with /admin/integrations (correctly shows "DEGRADED
    // · TEST mode"). Read the richer per-integration status instead.
    api.get("/admin/integrations/health").then(r => {
      const s = (r.data?.results || []).find(x => x.id === "stripe");
      setStripe(s || null);
    }).catch(() => {});
  }, []);

  const stripeLabel = (() => {
    if (!stripe) return "?";
    if (stripe.status === "ok") return "configured";
    if (stripe.status === "warn") return "test mode";
    if (stripe.status === "missing") return "not-set";
    return "error";
  })();

  return (
    <div data-testid="cockpit-mode-board"
         style={{ background: C.panel, border: `1px solid ${C.border}`,
                  borderRadius: 12, padding: 14, marginTop: 14 }}>
      <div style={{ fontFamily: C.mono, fontSize: 10, letterSpacing: "0.14em",
                    color: C.faint, marginBottom: 10 }}>
        MODE BOARD
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Chip label={`env: ${ver?.environment || "?"}`} />
        <Chip label={`deploy: ${ver?.commit_sha || "?"}`} mono />
        {ver?.last_github_push && (
          <Chip label={`gh: ${ver.last_github_push.commit_sha || "?"}`} mono />
        )}
        <Chip label={`stripe: ${stripeLabel}`} />
      </div>
    </div>
  );
}

function Chip({ label, mono }) {
  return (
    <span style={{
      padding: "4px 10px", borderRadius: 999,
      border: `1px solid ${C.border}`,
      fontSize: 11, color: C.dim,
      fontFamily: mono ? C.mono : "inherit",
    }}>{label}</span>
  );
}

function EnvBadge({ env, dbHost }) {
  // Big loud badge on preview so a founder never reads preview
  // metrics thinking they're production. Production stays subtle.
  const isPreview = env === "preview";
  const isProd    = env === "production";
  const bg  = isPreview ? "rgba(245,165,36,0.15)"
            : isProd    ? "rgba(34,197,94,0.15)"
            : "rgba(255,255,255,0.05)";
  const bd  = isPreview ? C.amber
            : isProd    ? C.green
            : C.border;
  const txt = isPreview ? C.amber
            : isProd    ? C.green
            : C.dim;
  return (
    <span data-testid="env-badge"
      data-env={env}
      title={`db: ${dbHost}`}
      style={{
        padding: "3px 9px", borderRadius: 999,
        border: `1px solid ${bd}`,
        background: bg,
        color: txt,
        fontFamily: C.mono, fontSize: 10, fontWeight: 700,
        letterSpacing: "0.14em",
      }}>
      {isPreview ? "PREVIEW DATA" : env.toUpperCase()}
    </span>
  );
}

function AnalogGauge({ value, max, label, sublabel, color, testId }) {
  // 2026-09-28 MASTER TASK Part B — semi-circle needle gauge. Distinct
  // on purpose from MetricCard/HealthDonut below (plain numbers) — a
  // literal analog dial per the founder's spec for these 3 activity
  // metrics specifically (total users / live now / active-24h).
  const pct = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  // 2026-09-28 "ACTIVATION FIRST" Part 3 fix — was `-90 + pct*180`,
  // which put 0% pointing UP and 100% pointing DOWN (off by 90° from
  // the arc's own declared endpoints, arcPoint(180)=left/arcPoint(0)
  // =right below). That mismatch is exactly why a 98%-full value
  // didn't look ~98%-full: the needle AND the filled-arc's endpoint
  // (both driven by this one `angleDeg`) were sweeping through the
  // BOTTOM of the dial instead of the TOP, and clipping short of
  // "right" well before 100%. Correct mapping: 0% = 180° (left),
  // 50% = 270° (top), 100% = 360°/0° (right).
  const angleDeg = 180 + pct * 180;
  const rad = (angleDeg * Math.PI) / 180;
  const cx = 50, cy = 54, r = 40;
  const needleX = cx + r * 0.82 * Math.cos(rad);
  const needleY = cy + r * 0.82 * Math.sin(rad);
  // Arc path helper (SVG polar → cartesian on the same circle).
  const arcPoint = (deg) => {
    const a = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const [x1, y1] = arcPoint(180); // left end (0%)
  const [x2, y2] = arcPoint(0);   // right end (100%)
  const [xm, ym] = arcPoint(angleDeg); // needle-tip end of the filled arc
  const bgArc = `M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`;
  const fgArc = `M ${x1} ${y1} A ${r} ${r} 0 0 1 ${xm} ${ym}`;
  return (
    <div data-testid={testId} style={{
      background: C.panel, border: `1px solid ${C.border}`,
      borderRadius: 12, padding: "14px 16px 10px", textAlign: "center",
    }}>
      <svg viewBox="0 0 100 62" width="100%" height="86">
        <path d={bgArc} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="6" strokeLinecap="round" />
        {pct > 0 && <path d={fgArc} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" />}
        <line x1={cx} y1={cy} x2={needleX} y2={needleY} stroke={C.text} strokeWidth="1.6" strokeLinecap="round" />
        <circle cx={cx} cy={cy} r="2.6" fill={C.text} />
      </svg>
      <div style={{ color: C.text, fontSize: 20, fontWeight: 700, marginTop: -6 }}>
        {value.toLocaleString()}
      </div>
      <div style={{ fontFamily: C.mono, fontSize: 10, letterSpacing: "0.12em",
                    color: C.faint, marginTop: 2 }}>
        {label}
      </div>
      {sublabel && (
        <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{sublabel}</div>
      )}
    </div>
  );
}

function PresenceGauges() {
  // 2026-09-28 MASTER TASK Part B — "Total users" / "Live now" /
  // "active-24h" chosen over "paid users" for the 3rd gauge: paid-user
  // count is already surfaced twice below (TOTAL USERS card's "X free
  // · Y paid" sub-line, and the PAID UPGRADES 30d card) as a revenue
  // metric. Pairing "Live now" with "Active last 24h" instead gives a
  // recency funnel (whole base → active today → active this instant)
  // that a dial is actually good at showing, since both are naturally
  // 0–100% of the user base — paid_users is still returned by
  // /admin/presence/summary for anyone who wants it back.
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.get("/admin/presence/summary", { timeout: 12000 })
      .then((r) => setD(r.data))
      .catch((e) => setErr(e?.message || "failed"));
  }, []);
  if (err) {
    return (
      <div data-testid="cockpit-presence-error" style={{
        color: C.amber, fontFamily: C.mono, fontSize: 11, marginTop: 14,
        padding: "8px 12px", border: `1px solid ${C.border}`, borderRadius: 8,
      }}>
        Presence gauges unavailable — {err}
      </div>
    );
  }
  if (!d) return null;
  const totalMilestone = Math.max(50, Math.ceil((d.total_users || 1) / 50) * 50);
  return (
    <div data-testid="cockpit-presence-gauges" style={{
      display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))",
      gap: 10, marginTop: 14,
    }}>
      <AnalogGauge testId="cockpit-gauge-total-users"
        value={d.total_users} max={totalMilestone} color={C.amber}
        label="TOTAL USERS" sublabel={`of ${totalMilestone} milestone`} />
      <AnalogGauge testId="cockpit-gauge-live-now"
        value={d.live_now} max={Math.max(1, d.total_users)} color={C.green}
        label="LIVE NOW" sublabel="active in the last 2 min" />
      <AnalogGauge testId="cockpit-gauge-active-24h"
        value={d.active_24h} max={Math.max(1, d.total_users)} color={C.amber}
        label="ACTIVE LAST 24H" sublabel="of total user base" />
    </div>
  );
}

function BusinessPulse() {
  const [d, setD] = useState(null);
  const [p, setP] = useState(null);
  // 2026-02-18 — Also pull /admin/financials so TOTAL USERS card can
  // show the tier breakdown as a sub-line. This resolves the "cockpit
  // says 33 total, financials says 31 free" confusion — same DB, but
  // one measures the whole `dev_users` collection (correct total) and
  // the other measures only the `tier=='free'` bucket. Showing both
  // in one place removes the false-discrepancy signal.
  const [tiers, setTiers] = useState(null);
  const [fin, setFin] = useState(null);
  const [presence, setPresence] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    // Feb 2026 · prod-hang fix — bound each fetch with a per-request
    // timeout so a slow /admin/pulse never leaves the Business Pulse
    // section blank forever.  Errors are captured and rendered so
    // the founder sees a visible failure instead of an empty grid.
    api.get("/admin/dashboard", { timeout: 12000 })
       .then(r => setD(r.data))
       .catch(e => setErr(prev => prev || `dashboard: ${e?.message || "failed"}`));
    api.get("/admin/pulse", { timeout: 12000 })
       .then(r => setP(r.data))
       .catch(e => setErr(prev => prev || `pulse: ${e?.message || "failed"}`));
    api.get("/admin/financials", { timeout: 12000 })
       .then(r => { setTiers(r.data?.users || null); setFin(r.data); })
       .catch(() => { /* soft-fail — the tier sub-line just won't render */ });
    // 2026-09-28 "ACTIVATION FIRST" Part 3 — TOTAL USERS/DAU now read
    // from the SAME /admin/presence/summary this page's own gauges
    // use (PresenceGauges above), instead of yet a 3rd source
    // (/admin/dashboard's raw count + narrow "sent a chat msg" DAU).
    // Fixes both the "246 vs 243" gauge/card mismatch AND "Live now 2
    // but DAU 0" (DAU is now "active in the last 24h", not "sent a
    // message today").
    api.get("/admin/presence/summary", { timeout: 12000 })
       .then(r => setPresence(r.data))
       .catch(() => { /* soft-fail — falls back to the old sources below */ });
  }, []);
  const users = presence?.total_users ?? p?.total_users ?? d?.total_users ?? 0;
  const dau   = presence?.active_24h ?? d?.dau ?? d?.dau_today ?? 0;
  // 2026-08-26 — was `d?.revenue_30d || d?.mrr`, both fields that
  // /admin/dashboard never returns (grep-confirmed) — this card was
  // permanently hardwired to show $0 next to a real non-zero "PAID
  // UPGRADES 30d" count. /admin/financials (already fetched on this
  // page for the tier sub-line) has the real, computed MRR.
  const rev   = fin?.metrics?.mrr_usd ?? 0;
  const ghPct = p?.github_connect_pct;
  const paidNew = p?.paid_new_30d;
  const tierSub = tiers
    ? `${tiers.free || 0} free · ${(tiers.starter || 0) + (tiers.pro || 0) + (tiers.team || 0)} paid`
    : null;
  return (
    <div data-testid="cockpit-business-pulse"
         style={{ display: "grid",
                  gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))",
                  gap: 10, marginTop: 14 }}>
      <MetricCard label="TOTAL USERS" value={users.toLocaleString()} sub={tierSub} />
      <MetricCard label="DAU (today)" value={dau.toLocaleString()} />
      <MetricCard label="REVENUE 30d" value={`$${Number(rev).toLocaleString()}`} />
      {ghPct != null && (
        <MetricCard label="GITHUB CONNECT %"
                    value={`${ghPct}%`}
                    sub={`${p?.github_connected || 0} of ${p?.total_users || 0}`} />
      )}
      {paidNew != null && (
        <MetricCard label="PAID UPGRADES 30d"
                    value={paidNew.toLocaleString()}
                    sub={`${p?.paid_users || 0} paid total`} />
      )}
      {err && (
        <div data-testid="cockpit-pulse-error"
             style={{ gridColumn: "1 / -1", color: C.amber, fontSize: 11,
                      fontFamily: C.mono, padding: "6px 10px",
                      border: `1px solid ${C.border}`, borderRadius: 8 }}>
          Business Pulse partial — {err}
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, sub }) {
  return (
    <div style={{
      background: C.panel, border: `1px solid ${C.border}`,
      borderRadius: 12, padding: 14,
    }}>
      <div style={{ fontFamily: C.mono, fontSize: 10,
           letterSpacing: "0.14em", color: C.faint, marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ color: C.text, fontSize: 22, fontWeight: 600 }}>{value}</div>
      {sub && (
        <div style={{ color: C.faint, fontSize: 10, marginTop: 3, fontFamily: C.mono }}>
          {sub}
        </div>
      )}
    </div>
  );
}

export default function AdminCockpit() {
  const { payload, err, loading, refresh } = useCockpitData();
  const checks = useMemo(() => payload?.checks || [], [payload]);
  const counts = payload?.counts || {};
  const envName = payload?.env || null;
  const dbHost  = payload?.db_host || "";

  const handleAck = async (checkId, hours) => {
    try {
      if (hours > 0) {
        const until = new Date(Date.now() + hours * 3600 * 1000).toISOString();
        await api.post(`/admin/status/${checkId}/ack?until=${encodeURIComponent(until)}`);
      } else {
        await api.post(`/admin/status/${checkId}/ack?until=`);
      }
      refresh();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("ack failed", e);
    }
  };

  return (
    <div data-testid="admin-cockpit-page"
         style={{ padding: 24, background: C.bg, minHeight: "100vh", color: C.text }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                      alignItems: "flex-start" }}>
          <div>
            <div style={{ fontFamily: C.mono, fontSize: 11,
                          letterSpacing: "0.18em", color: C.faint, marginBottom: 12,
                          display: "flex", gap: 10, alignItems: "center" }}>
              <span>COCKPIT · LIVE</span>
              {envName && <EnvBadge env={envName} dbHost={dbHost} />}
            </div>
            <div style={{ fontSize: 24, marginBottom: 20 }}>System Overview</div>
          </div>
          {/* Feb 2026 · Sidebar-in-cockpit — Admin shell already renders
              the NotificationBell in its sticky top bar, so we skip it
              here to avoid a duplicate bell when this page is rendered
              inside <Admin>. */}
        </div>

        {/* Feb 2026 · prod-hang fix — the System Health section (which
            depends on the /admin/status/all aggregator) is now
            DECOUPLED from the rest of the page.  Even when the
            aggregator hangs or errors, ModeBoard + BusinessPulse
            below still fetch their own data and render.  This keeps
            the cockpit useful in a degraded state instead of
            trapping the founder on an indefinite skeleton. */}
        <section data-testid="cockpit-system-health-section"
                 style={{ marginBottom: 32 }}>
          {loading && (
            <div data-testid="cockpit-status-loading"
                 style={{ color: C.faint, padding: "12px 0" }}>
              loading system-health checks…
            </div>
          )}
          {err && (
            <div data-testid="cockpit-error"
                 style={{ color: C.amber, fontFamily: C.mono, fontSize: 12,
                          padding: "10px 14px", border: `1px solid ${C.border}`,
                          borderRadius: 8, marginBottom: 16 }}>
              System Health unavailable — {err}
              <button data-testid="cockpit-error-retry"
                      onClick={refresh}
                      style={{ marginLeft: 12, background: "transparent",
                               border: `1px solid ${C.border}`, color: C.dim,
                               fontFamily: C.mono, fontSize: 11,
                               padding: "3px 8px", borderRadius: 5,
                               cursor: "pointer" }}>
                retry
              </button>
            </div>
          )}
          {!loading && !err && (
            <>
              <HealthDonut counts={counts} />
              <CheckList title="NEEDS ATTENTION (real failures)"
                         filterFn={(c) => c.status === "red" && !c.ack_active}
                         checks={checks}
                         testid="cockpit-needs-attention"
                         onAck={handleAck} />
              <CheckList title="ACKED (muted, still tracked)"
                         filterFn={(c) => c.ack_active}
                         checks={checks}
                         testid="cockpit-acked"
                         onAck={handleAck} />
              <CheckList title="SETUP PENDING (gray — config missing)"
                         filterFn={(c) => c.status === "gray"}
                         checks={checks}
                         testid="cockpit-setup-pending"
                         onAck={handleAck} />
              <CheckList title="ALL GREEN"
                         filterFn={(c) => c.status === "green" && !c.ack_active}
                         checks={checks}
                         testid="cockpit-all-green"
                         onAck={handleAck} />
            </>
          )}
        </section>

        {/* ModeBoard + BusinessPulse render UNCONDITIONALLY so a
            slow status/all can't block the business surfaces below. */}
        <ModeBoard />
        <PresenceGauges />
        <BusinessPulse />

        {/* 2026-02-18 — Live Business Intelligence merged in from the
            legacy /admin/financials page. Single source of truth so the
            cockpit and the (former) financials page can no longer
            disagree on MRR / active-subs / Stripe status. */}
        <LiveBusinessIntelligence />

        {/* 2026-08-23 — Codebase Health Score. Deliberately its own
            section, not folded into the guard-check donut above: this
            scores AUREM's OWN codebase (security/coverage/perf/etc),
            not live production checks. */}
        <HealthScoreWidget />

        {/* 2026-08-26 — Phase 3a follow-up: churn × complexity risk
            ranking (services/churn_risk.py), directly under the Health
            Score section since it's the same "scan our own codebase"
            family, not a production/business metric. */}
        <ChurnRiskWidget />

        {/* 2026-08-26 — Phase 3a follow-up: code duplication (jscpd).
            Kept as its own small Cockpit card rather than growing
            AdminQADashboard.jsx further (already flagged approaching
            the file-size guideline in the last code review). */}
        <DuplicationWidget />
      </div>
    </div>
  );
}
