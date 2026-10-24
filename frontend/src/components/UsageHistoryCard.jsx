/**
 * UsageHistoryCard.jsx — 2026-09-28 MASTER TASK Batch 2 (d), extended
 * Master Prompt v2 PART 4.3.
 *
 * Settings → Plans & Usage tab. The ONE allowed exception to "never
 * show per-action numbers anywhere" — a separate, opt-in audit
 * surface so a user can see/dispute their own usage. Was day-only
 * (date, tokens, calls) via GET /usage/me/history; now reads GET
 * /usage/me/ledger instead, which groups by (date, action) so each
 * row also carries an action name, per the PART 4.3 spec ("ledger
 * rows: date, action name, tokens").
 *
 * Standing rule (user visibility): date/action/tokens only — the
 * backend endpoint itself never returns cost, model, or provider
 * (projected away at the Mongo level, not just hidden here), so
 * there is nothing to accidentally render either.
 */
import React, { useEffect, useState } from "react";
import { History, Loader2 } from "lucide-react";
import { api } from "../lib/api";

export default function UsageHistoryCard() {
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let dead = false;
    api.get("/usage/me/ledger?days=30")
      .then((r) => { if (!dead) setRows(r.data?.rows || []); })
      .catch(() => { if (!dead) setRows([]); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, []);

  return (
    <section className="card" data-testid="settings-usage-history">
      <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
        <History size={14} /> Usage history
      </h3>
      <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 14px" }}>
        Your token activity for the last 30 days — use this to audit or dispute a charge.
      </p>
      {loading ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--muted)", fontSize: 13, padding: "8px 0" }}>
          <Loader2 size={14} className="animate-spin" /> Loading…
        </div>
      ) : rows.length === 0 ? (
        <div data-testid="usage-history-empty" style={{ fontSize: 13, color: "var(--text-faint)", padding: "8px 0" }}>
          No activity in the last 30 days.
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {rows.map((r, i) => (
            <div key={`${r.date}-${r.action}-${i}`} data-testid="usage-history-row"
                 style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "6px 0", borderBottom: "1px solid var(--border-soft)" }}>
              <span style={{ color: "var(--text-faint)" }}>{r.date}</span>
              <span style={{ color: "var(--text)" }}>{r.action}</span>
              <span style={{ color: "var(--text-faint)" }}>{Number(r.tokens).toLocaleString()} tokens</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
