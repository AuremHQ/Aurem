/**
 * pages/AdminPricingPanel.jsx — Master Prompt v2 PART 3 admin panel.
 *
 * Margin editor (preview -> confirm, future purchases only), fixed
 * monthly costs, the realized-margin/break-even/ceiling-hit/drift
 * dashboard, and the billing_flags (refund/dispute/spike) review
 * list — all against routers/admin_pricing.py.
 *
 * Every user-supplied string rendered here (fixed-cost names, flag
 * resolution notes) goes through plain JSX text interpolation
 * (`{value}`), never `dangerouslySetInnerHTML` — React escapes that
 * automatically, which is the actual XSS defense; the backend stores
 * these strings RAW on purpose (see services/pricing_engine.py's
 * set_fixed_cost docstring) specifically so this page's normal
 * rendering is what keeps it safe, not a write-time mangling.
 */
import React, { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Badge, Card, MCard, Table, fmtMoney } from "./Admin";

function errText(e) {
  return e?.response?.data?.detail?.message || e?.response?.data?.detail || e.message || "Request failed";
}

function SectionTitle({ children }) {
  return <h2 style={{ fontSize: 14, fontWeight: 600, margin: "28px 0 10px", color: "var(--text)" }}>{children}</h2>;
}

function MarginEditor({ onChanged }) {
  const [current, setCurrent] = useState(null);
  const [marginPct, setMarginPct] = useState("");
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadCurrent() {
    try {
      const r = await api.get("/admin/pricing/version");
      setCurrent(r.data.current);
    } catch (e) { setErr(errText(e)); }
  }
  useEffect(() => { loadCurrent(); }, []);

  async function doPreview() {
    setErr(""); setPreview(null);
    const pct = parseFloat(marginPct);
    if (Number.isNaN(pct)) { setErr("Enter a margin percentage, e.g. 50"); return; }
    setBusy(true);
    try {
      const r = await api.post("/admin/pricing/margin/preview", { margin: pct / 100 });
      setPreview(r.data);
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  }

  async function doConfirm() {
    if (!preview) return;
    setBusy(true); setErr("");
    try {
      const p = preview.preview;
      await api.post("/admin/pricing/margin/confirm", {
        margin: preview.proposed_margin,
        stripe_fee_pct: p.stripe_fee_pct,
        stripe_fee_fixed_usd: p.stripe_fee_fixed_usd,
        expires_at: p.expires_at,
        token: p.token,
      });
      setPreview(null);
      setMarginPct("");
      await loadCurrent();
      onChanged && onChanged();
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  }

  return (
    <Card style={{ padding: 16 }} data-testid="margin-editor">
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 8 }}>
        Current margin: <b data-testid="current-margin">
          {current ? `${(current.margin * 100).toFixed(1)}%` : "…"}
        </b>
        {current && <span style={{ marginLeft: 8, color: "var(--text-faint)" }}>(version {current._id})</span>}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input
          type="number" step="0.1" min="10" max="80"
          placeholder="New margin % (10-80)"
          value={marginPct}
          onChange={(e) => { setMarginPct(e.target.value); setPreview(null); }}
          data-testid="margin-input"
          style={{ padding: "6px 10px", fontSize: 13, width: 220,
                   background: "var(--bg-elev)", border: "1px solid var(--border)",
                   borderRadius: 4, color: "var(--text)" }}
        />
        <button className="btn-primary" onClick={doPreview} disabled={busy} data-testid="margin-preview-btn">
          Preview
        </button>
      </div>
      {err && <div style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }} data-testid="margin-error">{err}</div>}
      {preview && (
        <div style={{ marginTop: 14, padding: 12, border: "1px dashed var(--border)", borderRadius: 4 }}
             data-testid="margin-preview-block">
          <div style={{ fontSize: 12, marginBottom: 8 }}>
            Changing margin from <b>{(preview.current_margin * 100).toFixed(1)}%</b> to{" "}
            <b>{(preview.proposed_margin * 100).toFixed(1)}%</b> — applies to <b>future purchases only</b>,
            never retroactively.
          </div>
          <table style={{ fontSize: 12, width: "100%" }}>
            <thead><tr>
              <th style={{ textAlign: "left" }}>Action</th>
              <th style={{ textAlign: "left" }}>7d avg cost</th>
              <th style={{ textAlign: "left" }}>Tokens today</th>
              <th style={{ textAlign: "left" }}>Tokens at new margin</th>
            </tr></thead>
            <tbody>
              {preview.impact_by_action.map((row) => (
                <tr key={row.action}>
                  <td>{row.action}</td>
                  <td>{row.avg_cost_usd_7d != null ? fmtMoney(row.avg_cost_usd_7d) : "no data yet"}</td>
                  <td>{row.tokens_at_current_margin ?? "—"}</td>
                  <td>{row.tokens_at_new_margin ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="btn-primary" onClick={doConfirm} disabled={busy}
                  style={{ marginTop: 10 }} data-testid="margin-confirm-btn">
            Confirm — apply new margin
          </button>
        </div>
      )}
    </Card>
  );
}

function FixedCosts() {
  const [rows, setRows] = useState([]);
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    try {
      const r = await api.get("/admin/pricing/fixed-costs");
      setRows(r.data.fixed_costs || []);
    } catch (e) { setErr(errText(e)); }
  }
  useEffect(() => { load(); }, []);

  async function add() {
    setErr("");
    const amt = parseFloat(amount);
    if (!name.trim() || Number.isNaN(amt)) { setErr("Name and a numeric monthly amount are required"); return; }
    try {
      await api.post("/admin/pricing/fixed-costs", { name, amount_usd_monthly: amt });
      setName(""); setAmount("");
      await load();
    } catch (e) { setErr(errText(e)); }
  }

  async function remove(id) {
    try {
      await api.delete(`/admin/pricing/fixed-costs/${id}`);
      await load();
    } catch (e) { setErr(errText(e)); }
  }

  return (
    <Card style={{ padding: 16 }} data-testid="fixed-costs">
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input placeholder="Cost name (e.g. Hosting)" value={name}
               onChange={(e) => setName(e.target.value)} data-testid="fixed-cost-name-input"
               style={{ padding: "6px 10px", fontSize: 13, flex: 1,
                        background: "var(--bg-elev)", border: "1px solid var(--border)",
                        borderRadius: 4, color: "var(--text)" }} />
        <input placeholder="$/month" type="number" value={amount}
               onChange={(e) => setAmount(e.target.value)} data-testid="fixed-cost-amount-input"
               style={{ padding: "6px 10px", fontSize: 13, width: 120,
                        background: "var(--bg-elev)", border: "1px solid var(--border)",
                        borderRadius: 4, color: "var(--text)" }} />
        <button className="btn-primary" onClick={add} data-testid="fixed-cost-add-btn">Add</button>
      </div>
      {err && <div style={{ color: "var(--danger)", fontSize: 12, marginBottom: 8 }}>{err}</div>}
      <Table
        cols={["Name", "$/month", ""]}
        rows={rows.map((r) => [
          r.name,
          fmtMoney(r.amount_usd_monthly),
          <button key="del" onClick={() => remove(r._id)} data-testid={`fixed-cost-delete-${r._id}`}
                  style={{ fontSize: 11, color: "var(--danger)", background: "none",
                           border: "none", cursor: "pointer" }}>
            Remove
          </button>,
        ])}
      />
    </Card>
  );
}

function Dashboard({ refreshKey }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");

  async function load() {
    try {
      const r = await api.get("/admin/pricing/dashboard");
      setD(r.data);
    } catch (e) { setErr(errText(e)); }
  }
  useEffect(() => { load(); }, [refreshKey]);

  if (err) return <Card style={{ padding: 16, color: "var(--danger)", fontSize: 12 }}>{err}</Card>;
  if (!d) return <div style={{ padding: 16, color: "var(--text-faint)", fontSize: 12 }}>Loading…</div>;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 12 }}>
        <MCard label="Tokens owed" value={fmtMoney(d.tokens_owed_usd)} sub={`${d.tokens_owed} tokens outstanding`} />
        <MCard label="Fixed costs (mo)" value={fmtMoney(d.fixed_costs_monthly_usd)} />
        <MCard label="Break-even progress"
               value={d.break_even_progress_pct != null ? `${d.break_even_progress_pct}%` : "n/a"}
               accent={d.break_even_progress_pct >= 100 ? "var(--ok)" : "var(--text)"} />
        <MCard label="Failed-run cost" value={fmtMoney(d.failed_run_cost.total_usd)}
               sub={`${d.failed_run_cost.count} runs`} accent="var(--danger)" />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 12, marginBottom: 12 }}>
        <MCard label="Ceiling hits" value={d.ceiling_hit.count}
               sub={`${fmtMoney(d.ceiling_hit.absorbed_usd)} absorbed by the platform`} />
        <MCard label="Unmetered-cost alarm"
               value={d.unmetered_cost_alarm.alarm ? "ALARM" : "clear"}
               accent={d.unmetered_cost_alarm.alarm ? "var(--danger)" : "var(--ok)"}
               sub={`gap ${fmtMoney(d.unmetered_cost_alarm.gap_usd)} over 7d`} />
      </div>

      <SectionTitle>Realized margin by action</SectionTitle>
      <Table
        cols={["Action", "Revenue", "Real cost", "Realized margin", "Runs"]}
        rows={d.by_action.map((r) => [
          r.action, fmtMoney(r.revenue_usd), fmtMoney(r.real_cost_usd),
          r.realized_margin != null ? `${(r.realized_margin * 100).toFixed(1)}%` : "—",
          r.runs,
        ])}
      />

      <SectionTitle>Top users by revenue</SectionTitle>
      <Table
        cols={["User", "Revenue", "Real cost", "Realized margin"]}
        rows={d.by_user_top50.slice(0, 10).map((r) => [
          r.user_id, fmtMoney(r.revenue_usd), fmtMoney(r.real_cost_usd),
          r.realized_margin != null ? `${(r.realized_margin * 100).toFixed(1)}%` : "—",
        ])}
      />

      <SectionTitle>Reconcile drift</SectionTitle>
      <div style={{ fontSize: 12, color: "var(--text-faint)" }}>
        {d.reconcile_drift.flagged_count} user(s) flagged &gt;{3}% drift
        out of {d.reconcile_drift.users_compared} compared this month.
      </div>
    </div>
  );
}

function BillingFlags() {
  const [tab, setTab] = useState("open");
  const [rows, setRows] = useState([]);
  const [note, setNote] = useState({});
  const [err, setErr] = useState("");

  async function load(which) {
    try {
      const r = await api.get(`/admin/pricing/billing-flags?resolved=${which === "resolved"}`);
      setRows(r.data.flags || []);
    } catch (e) { setErr(errText(e)); }
  }
  useEffect(() => { load(tab); }, [tab]);

  async function resolve(id) {
    try {
      await api.post(`/admin/pricing/billing-flags/${id}/resolve`, { note: note[id] || "" });
      await load(tab);
    } catch (e) { setErr(errText(e)); }
  }

  return (
    <Card style={{ padding: 16 }} data-testid="billing-flags">
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <button onClick={() => setTab("open")}
                className={tab === "open" ? "btn-primary" : ""} data-testid="flags-tab-open">Open</button>
        <button onClick={() => setTab("resolved")}
                className={tab === "resolved" ? "btn-primary" : ""} data-testid="flags-tab-resolved">Resolved</button>
      </div>
      {err && <div style={{ color: "var(--danger)", fontSize: 12, marginBottom: 8 }}>{err}</div>}
      {rows.length === 0 && <div style={{ fontSize: 12, color: "var(--text-faint)" }}>No flags.</div>}
      {rows.map((f) => (
        <div key={f._id} style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}
             data-testid={`billing-flag-${f._id}`}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
            <Badge>{f.flag_type}</Badge>
            <span style={{ fontSize: 12 }}>{f.user_id}</span>
            <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
              {new Date(f.created_at * 1000).toLocaleString()}
            </span>
          </div>
          <pre style={{ fontSize: 11, color: "var(--text-faint)", margin: "4px 0" }}>
            {JSON.stringify(f.detail || {})}
          </pre>
          {f.resolved ? (
            <div style={{ fontSize: 11, color: "var(--ok)" }}>
              Resolved by {f.resolved_by}{f.resolution_note ? `: ${f.resolution_note}` : ""}
            </div>
          ) : (
            <div style={{ display: "flex", gap: 8 }}>
              <input placeholder="resolution note (optional)"
                     value={note[f._id] || ""}
                     onChange={(e) => setNote({ ...note, [f._id]: e.target.value })}
                     style={{ padding: "4px 8px", fontSize: 12, flex: 1,
                              background: "var(--bg-elev)", border: "1px solid var(--border)",
                              borderRadius: 4, color: "var(--text)" }} />
              <button onClick={() => resolve(f._id)} data-testid={`resolve-flag-${f._id}`}
                      style={{ fontSize: 11 }}>
                Mark resolved
              </button>
            </div>
          )}
        </div>
      ))}
    </Card>
  );
}

export default function AdminPricingPanel() {
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <div style={{ padding: "24px 20px", maxWidth: 1100 }}>
      <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 16px", color: "var(--text)" }}>
        Pricing Panel
      </h1>
      <SectionTitle>Margin</SectionTitle>
      <MarginEditor onChanged={() => setRefreshKey((k) => k + 1)} />
      <SectionTitle>Fixed monthly costs</SectionTitle>
      <FixedCosts />
      <SectionTitle>Dashboard</SectionTitle>
      <Dashboard refreshKey={refreshKey} />
      <SectionTitle>Billing flags</SectionTitle>
      <BillingFlags />
    </div>
  );
}
