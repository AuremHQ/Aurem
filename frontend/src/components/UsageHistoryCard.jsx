/**
 * UsageHistoryCard.jsx — 2026-09-28 MASTER TASK Batch 2 (d)
 * Settings → Plans & Usage tab. Per-day token/activity history for
 * the signed-in user only, from GET /usage/me/history.
 *
 * Standing rule (user visibility): tokens and call counts only — the
 * backend endpoint itself never returns cost, model, or provider, so
 * there is nothing here to accidentally render either.
 */
import React, { useEffect, useState } from "react";
import { History, Loader2 } from "lucide-react";
import { api } from "../lib/api";

export default function UsageHistoryCard() {
  const [days, setDays] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let dead = false;
    api.get("/usage/me/history?days=30")
      .then((r) => { if (!dead) setDays(r.data?.days || []); })
      .catch(() => { if (!dead) setDays([]); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, []);

  return (
    <section className="card" data-testid="settings-usage-history">
      <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
        <History size={14} /> Usage history
      </h3>
      <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 14px" }}>
        Your token activity for the last 30 days.
      </p>
      {loading ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--muted)", fontSize: 13, padding: "8px 0" }}>
          <Loader2 size={14} className="animate-spin" /> Loading…
        </div>
      ) : days.length === 0 ? (
        <div data-testid="usage-history-empty" style={{ fontSize: 13, color: "var(--text-faint)", padding: "8px 0" }}>
          No activity in the last 30 days.
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {days.map((d) => (
            <div key={d.date} data-testid="usage-history-row"
                 style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "6px 0", borderBottom: "1px solid var(--border-soft)" }}>
              <span style={{ color: "var(--text-faint)" }}>{d.date}</span>
              <span style={{ color: "var(--text)" }}>{Number(d.tokens).toLocaleString()} tokens</span>
              <span style={{ color: "var(--text-faint)" }}>{d.calls} {d.calls === 1 ? "activity" : "activities"}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
