/**
 * AgentTokenPanel.jsx — Iter 65 — Per-provider token consumption widget
 * shown above the Users list in the admin panel.
 *
 * 2026-09-27 MASTER TASK Part A/C — rewritten to render whatever
 * providers actually show up in `real_llm_usage` (the one canonical
 * ledger) instead of a hardcoded 4-agent list with a mislabeled
 * "per 1k" rate table baked into the tooltip (that was the literal
 * source of the "$0.30 per 1k" ~1000x-too-high bug). Every number
 * here now comes straight from `/admin/agent-tokens`, which itself
 * reads real provider-reported tokens + real cost_usd — no fixed
 * rate table anywhere in this file anymore.
 *
 * Range selector: 24h | 7d | 30d | 90d | 365d.
 * Lightweight inline bar chart (no charting lib) for the time series.
 */
import React, { useEffect, useState, useMemo } from "react";
import { api, getToken } from "../lib/api";

const RANGES = [
  { id: "24h",  label: "24h",  desc: "hourly" },
  { id: "7d",   label: "7d",   desc: "daily" },
  { id: "30d",  label: "30d",  desc: "daily" },
  { id: "90d",  label: "90d",  desc: "weekly" },
  { id: "365d", label: "1y",   desc: "monthly" },
];

// Color per provider — falls back to a stable hash-based color for
// any provider not in this list, so a brand-new model never renders
// blank/uncolored.
const PROVIDER_COLORS = {
  deepseek:         "#ff8a2a",
  deepseek_direct:  "#ff8a2a",
  anthropic:        "#ffc560",
  "z-ai":           "#6aa9ff",
  openai:           "#c792ea",
  groq:             "#6dd4a1",
};
const FALLBACK_COLORS = ["#e05d5d", "#5dc9c9", "#a0e05d", "#e0a05d"];
function colorFor(provider, idx) {
  return PROVIDER_COLORS[provider] || FALLBACK_COLORS[idx % FALLBACK_COLORS.length];
}
function labelFor(provider) {
  if (provider === "z-ai") return "GLM";
  if (provider === "anthropic") return "Claude";
  if (provider === "deepseek_direct") return "DeepSeek (direct)";
  if (provider === "unknown") return "Unattributed";
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

export default function AgentTokenPanel() {
  const [range, setRange] = useState("7d");
  const [data,  setData]  = useState(null);
  const [error, setError] = useState(null);
  const [busy,  setBusy]  = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setBusy(true);
      setError(null);
      try {
        const r = await api.get(`/admin/agent-tokens?range=${range}`, {
          headers: { Authorization: `Bearer ${getToken()}` },
        });
        if (!cancelled) setData(r.data);
      } catch (e) {
        if (!cancelled) setError(e?.response?.data?.detail || e.message);
      } finally {
        if (!cancelled) setBusy(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [range]);

  const providers = data?.providers || [];

  // Pre-compute chart bar max so heights normalize
  const maxBucketTotal = useMemo(() => {
    if (!data?.series) return 0;
    return data.series.reduce((m, b) =>
      Math.max(m, providers.reduce((s, p) => s + (b[p] || 0), 0)),
      0);
  }, [data, providers]);

  return (
    <div data-testid="agent-token-panel" style={{
      background: "var(--panel)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      padding: "14px 16px",
      marginBottom: 18,
    }}>
      {/* Header + range selector */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        flexWrap: "wrap", marginBottom: 14,
      }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
            Agent token P&amp;L
          </div>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         letterSpacing: "0.05em", marginTop: 2 }}>
            Real per-call cost from real_llm_usage — every provider that actually ran.
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 4,
                       flexWrap: "wrap" }}>
          {RANGES.map((r) => (
            <button
              key={r.id}
              data-testid={`agent-tokens-range-${r.id}`}
              onClick={() => setRange(r.id)}
              style={{
                padding: "5px 11px", fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
                border: `1px solid ${range === r.id
                  ? "var(--accent-2)" : "var(--border)"}`,
                background: range === r.id
                  ? "var(--accent-soft)" : "transparent",
                color: range === r.id
                  ? "var(--accent-2)" : "var(--text-dim)",
                borderRadius: 4, cursor: "pointer",
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div data-testid="agent-tokens-error" style={{
          padding: "8px 10px",
          background: "rgba(226,75,74,0.1)",
          border: "1px solid rgba(226,75,74,0.3)",
          borderRadius: 6, fontSize: 11, color: "var(--danger)",
        }}>
          {error}
        </div>
      )}

      {busy && !data && (
        <div style={{ fontSize: 11, color: "var(--text-faint)",
                       fontStyle: "italic" }}>Loading…</div>
      )}

      {data && providers.length === 0 && (
        <div data-testid="agent-tokens-empty" style={{
          fontSize: 11, color: "var(--text-faint)", fontStyle: "italic",
        }}>
          No real LLM calls recorded in this window yet.
        </div>
      )}

      {data && providers.length > 0 && (
        <>
          {/* Per-provider summary cards */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
            gap: 10, marginBottom: 14,
          }}>
            {providers.map((provider, idx) => {
              const tokens = data.totals_tokens?.[provider] || 0;
              const cost = data.costs_usd?.[provider] || 0;
              const calls = data.call_counts?.[provider] || 0;
              const tasks = data.task_counts?.[provider] || 0;
              const avg = data.avg_per_call?.[provider] || {};
              return (
                <div key={provider} data-testid={`agent-card-${provider}`} style={{
                  padding: "10px 12px",
                  background: "var(--panel-2)",
                  border: "1px solid var(--border)",
                  borderLeft: `3px solid ${colorFor(provider, idx)}`,
                  borderRadius: 4,
                }}>
                  <div style={{ fontSize: 11, color: "var(--text-dim)",
                                 letterSpacing: "0.05em" }}>
                    {labelFor(provider)}
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 600,
                                 color: "var(--text)", marginTop: 4,
                                 fontFamily: "'JetBrains Mono', monospace" }}>
                    ${cost.toFixed(4)}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-faint)",
                                 fontFamily: "'JetBrains Mono', monospace" }}>
                    {tokens.toLocaleString()} tok · {calls} call{calls === 1 ? "" : "s"}
                    {tasks > 0 ? ` · ${tasks} task${tasks === 1 ? "" : "s"}` : ""}
                  </div>
                  {calls > 0 && (
                    <div style={{ fontSize: 9, color: "var(--text-faint)",
                                   marginTop: 4 }}>
                      avg ${avg.cost_avg_usd?.toFixed(6) || "0.000000"}/call
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Claude-vs-DeepSeek delta — the headline answer, when both ran */}
          {data.claude_vs_deepseek && (
            <div data-testid="claude-vs-deepseek" style={{
              padding: "10px 12px", marginBottom: 14,
              background: "rgba(255,138,42,0.05)",
              border: "1px solid rgba(255,138,42,0.25)",
              borderRadius: 4,
              fontSize: 11, color: "var(--text-dim)",
              fontFamily: "'JetBrains Mono', monospace",
              overflowWrap: "anywhere",
            }}>
              <span style={{ color: "var(--accent-2)", fontWeight: 600 }}>
                Claude vs DeepSeek delta:{" "}
              </span>
              <span style={{ color: "var(--text)" }}>
                ${data.claude_vs_deepseek.delta_usd_per_call.toFixed(6)} extra per call
              </span>
              {data.claude_vs_deepseek.delta_multiplier && (
                <> · <span style={{ color: "var(--accent-2)" }}>
                  {data.claude_vs_deepseek.delta_multiplier}×
                </span> the DeepSeek cost</>
              )}
            </div>
          )}

          {/* Stacked-bar series — no chart lib, pure CSS */}
          {data.series && data.series.length > 0 && (
            <div data-testid="agent-tokens-chart" style={{
              display: "flex", alignItems: "flex-end", gap: 6,
              height: 90, padding: "0 4px",
              borderBottom: "1px solid var(--border)",
              overflowX: "auto", overflowY: "hidden",
            }}>
              {data.series.map((bucket, i) => {
                const total = providers.reduce((s, p) => s + (bucket[p] || 0), 0);
                const heightPct = maxBucketTotal
                  ? (total / maxBucketTotal) * 100 : 0;
                return (
                  <div key={i} style={{
                    display: "flex", flexDirection: "column",
                    alignItems: "center", gap: 3, minWidth: 18, flex: 1,
                  }}>
                    <div style={{
                      width: "100%", height: `${heightPct}%`,
                      minHeight: total > 0 ? 2 : 0,
                      display: "flex", flexDirection: "column-reverse",
                      borderRadius: "2px 2px 0 0", overflow: "hidden",
                    }}
                    title={`${bucket.label}: ${total.toLocaleString()} tokens`}>
                      {providers.map((p, idx) => {
                        const v = bucket[p] || 0;
                        if (!v) return null;
                        return (
                          <div key={p} style={{
                            background: colorFor(p, idx),
                            height: `${(v / total) * 100}%`,
                            opacity: 0.9,
                          }} />
                        );
                      })}
                    </div>
                    <div style={{ fontSize: 8, color: "var(--text-faint)",
                                   whiteSpace: "nowrap" }}>
                      {bucket.label}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-faint)",
                         display: "flex", gap: 14, flexWrap: "wrap" }}>
            <span data-testid="agent-tokens-total"><b style={{ color: "var(--text)" }}>
              Total: ${data.total_cost_usd?.toFixed(4)}
            </b></span>
            <span>Range: {data.range} · {data.bucket} buckets · {data.buckets_count} pts</span>
          </div>
        </>
      )}
    </div>
  );
}
