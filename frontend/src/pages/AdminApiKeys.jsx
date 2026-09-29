/**
 * pages/AdminApiKeys.jsx — MCP API key management UI.
 *
 * Wires to:
 *   POST   /api/aurem-dev/mcp/keys           → mint new key
 *   GET    /api/aurem-dev/mcp/keys           → list masked keys
 *   DELETE /api/aurem-dev/mcp/keys/{tail}    → revoke
 *
 * The full `sk-aurem-…` secret is returned exactly once by POST /keys
 * and shown to the user with a copy button + "this is the only time
 * you'll see it" warning. We never re-display the secret after the
 * banner is dismissed — the GET list returns masked values only.
 */
import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, getToken } from "../lib/api";
import { cleanErr } from "../lib/cleanErr";
import { toast } from "../components/Toast";

function timeSince(epoch) {
  // Iter 353 — non-numeric epochs rendered "NaNd ago" (founder audit).
  if (!epoch || !Number.isFinite(Number(epoch))) return "never used";
  const s = Math.max(1, Math.round(Date.now() / 1000 - Number(epoch)));
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export default function AdminApiKeys() {
  const nav = useNavigate();
  const [keys, setKeys]         = useState([]);
  const [loading, setLoading]   = useState(true);
  const [err, setErr]           = useState("");
  const [busy, setBusy]         = useState(false);
  // The newly-minted full key — shown ONCE then cleared from state.
  const [newKey, setNewKey]     = useState(null);

  const load = useCallback(async () => {
    setErr("");
    setLoading(true);
    try {
      const r = await api.get("/mcp/keys", {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setKeys(r.data?.keys || []);
    } catch (e) {
      const detail = cleanErr(e, "Failed to load keys.");
      setErr(detail);
      if (e?.response?.status === 401) {
        nav("/login?next=/admin/api-keys", { replace: true });
      }
    } finally {
      setLoading(false);
    }
  }, [nav]);

  useEffect(() => { load(); }, [load]);

  const mintKey = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.post("/mcp/keys", {}, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setNewKey(r.data?.key || "");
      await load();
    } catch (e) {
      const detail = cleanErr(e, "Failed to generate key.");
      setErr(detail);
      toast({ message: detail, kind: "error", duration: 3500 });
    } finally {
      setBusy(false);
    }
  };

  const revokeKey = async (masked) => {
    // Masked format from backend: "sk-aurem-XXXX…YYYY" — we use the
    // last 4 chars as the URL-safe revocation tail.
    const tail = masked.split("…").pop() || "";
    if (tail.length < 4) {
      toast({ message: "Could not parse key tail.", kind: "error" });
      return;
    }
    if (!window.confirm(`Revoke this key permanently? Tail: …${tail}`)) return;
    try {
      const r = await api.delete(`/mcp/keys/${encodeURIComponent(tail)}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      toast({
        message: `Revoked ${r.data?.revoked || 0} key(s).`,
        kind: "success", duration: 2500,
      });
      await load();
    } catch (e) {
      const detail = cleanErr(e, "Revoke failed.");
      toast({ message: detail, kind: "error", duration: 3500 });
    }
  };

  const copy = (text) => {
    navigator.clipboard.writeText(text);
    toast({ message: "Copied to clipboard.", kind: "success", duration: 1500 });
  };

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: "32px 24px" }}>
      <div style={{ marginBottom: 8, color: "var(--text-faint)", fontSize: 11,
                    letterSpacing: "0.18em", textTransform: "uppercase" }}>
        ADMIN · MCP
      </div>
      <h1 style={{ fontSize: 28, margin: "0 0 6px", letterSpacing: "-0.01em" }}>
        API Keys
      </h1>
      <p style={{ color: "var(--text-dim)", fontSize: 14, margin: "0 0 28px",
                  lineHeight: 1.6, maxWidth: 680 }}>
        Long-lived <code style={codeChip}>sk-aurem-…</code> keys for MCP
        clients (Claude Desktop, Cursor, Cline). Each key inherits your
        account&apos;s permissions and can be revoked anytime. Treat them
        like passwords — never commit them to git.
      </p>

      {/* Mint button */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 24 }}>
        <button
          data-testid="mint-mcp-key"
          onClick={mintKey}
          disabled={busy}
          style={btnPrimary(busy)}
        >
          {busy ? "Generating…" : "+ Generate new key"}
        </button>
        <button
          data-testid="refresh-mcp-keys"
          onClick={load}
          disabled={loading}
          style={btnGhost}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {err && (
        <div style={errBanner} data-testid="mcp-keys-err">{err}</div>
      )}

      {/* New-key reveal banner — shown EXACTLY once */}
      {newKey && (
        <div data-testid="new-mcp-key-banner" style={revealBanner}>
          <div style={{ fontSize: 11, color: "#ffb454",
                        letterSpacing: "0.12em", textTransform: "uppercase",
                        marginBottom: 8 }}>
            ⚠ Copy this now — it won&apos;t be shown again
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center",
                        flexWrap: "wrap" }}>
            <code data-testid="new-mcp-key-value" style={keyDisplay}>
              {newKey}
            </code>
            <button
              data-testid="copy-mcp-key"
              onClick={() => copy(newKey)}
              style={btnGhost}
            >
              Copy
            </button>
            <button
              data-testid="dismiss-mcp-key"
              onClick={() => setNewKey(null)}
              style={btnGhost}
            >
              I&apos;ve saved it
            </button>
          </div>
          <div style={{ fontSize: 12, color: "var(--text-dim)",
                        marginTop: 12, lineHeight: 1.6 }}>
            Use as <code style={codeChip}>Bearer {newKey.slice(0, 14)}…</code>{" "}
            in your MCP client config. Endpoint:{" "}
            <code style={codeChip}>https://auremcto.com/api/aurem-dev/mcp</code>
          </div>
        </div>
      )}

      {/* Existing keys table */}
      {/* Iter 354 — when the fetch failed, keys.length is a stale []
          — showing "(0)" falsely claimed an empty account (founder's
          3 real keys were masked during the origin 520 window). */}
      <h3 style={sectionLabel}>Active keys ({err ? "?" : keys.length})</h3>
      {loading && keys.length === 0 ? (
        <div style={emptyState}>Loading…</div>
      ) : keys.length === 0 ? (
        <div data-testid="mcp-keys-empty" style={emptyState}>
          No API keys yet. Click &quot;Generate new key&quot; above to create one.
        </div>
      ) : (
        <div style={tableWrap}>
          <table style={{ width: "100%", borderCollapse: "collapse",
                          fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)",
                           color: "var(--text-faint)",
                           textTransform: "uppercase",
                           fontSize: 10, letterSpacing: "0.1em" }}>
                <th style={th}>Key</th>
                <th style={th}>Created</th>
                <th style={th}>Last used</th>
                <th style={th}>Status</th>
                <th style={{ ...th, textAlign: "right" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k, i) => (
                <tr key={i} data-testid={`mcp-key-row-${i}`}
                    style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={td}>
                    <code style={codeChip}>{k.key_masked || "—"}</code>
                    {k.label && (
                      <span style={{ marginLeft: 8, fontSize: 11,
                                     color: "var(--text-faint)" }}>
                        {k.label}
                      </span>
                    )}
                  </td>
                  <td style={td}>{timeSince(k.created_at)}</td>
                  <td style={td}>{timeSince(k.last_used_at)}</td>
                  <td style={td}>
                    <span style={k.active ? badgeOk : badgeRevoked}>
                      {k.active ? "Active" : "Revoked"}
                    </span>
                  </td>
                  <td style={{ ...td, textAlign: "right" }}>
                    {k.active && (
                      <button
                        data-testid={`revoke-mcp-key-${i}`}
                        onClick={() => revokeKey(k.key_masked)}
                        style={btnDanger}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Claude Desktop config hint */}
      <div style={hintBox}>
        <div style={{ fontSize: 11, color: "var(--text-faint)",
                      letterSpacing: "0.12em", textTransform: "uppercase",
                      marginBottom: 10 }}>
          Claude Desktop · config snippet
        </div>
        <pre style={preBlock}>{`{
  "mcpServers": {
    "aurem-cto": {
      "url": "https://auremcto.com/api/aurem-dev/mcp",
      "headers": {
        "Authorization": "Bearer sk-aurem-YOUR_KEY_HERE"
      }
    }
  }
}`}</pre>
        <div style={{ fontSize: 11, color: "var(--text-dim)",
                      marginTop: 8, lineHeight: 1.6 }}>
          Paste into{" "}
          <code style={codeChip}>~/Library/Application Support/Claude/claude_desktop_config.json</code>
          {" "}(macOS) or{" "}
          <code style={codeChip}>%APPDATA%\Claude\claude_desktop_config.json</code>
          {" "}(Windows). Restart Claude Desktop after editing.
        </div>
      </div>

      <OraChatMcpCard />
    </div>
  );
}

// ── ORA Chat MCP (admin-only) — 2026-09-28 Part A ────────────────────
function OraChatMcpCard() {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [newKey, setNewKey] = useState(null);
  const [label, setLabel] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [testBusy, setTestBusy] = useState(false);
  const mcpUrl = `${window.location.origin}/api/aurem-dev/mcp/ora-chat`;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/gateway/keys?scope=mcp", {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setKeys(r.data?.keys || []);
    } catch (e) {
      toast({ message: cleanErr(e, "Failed to load MCP keys."), kind: "error", duration: 3000 });
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy(true);
    try {
      const r = await api.post("/admin/gateway/keys",
        { scope: "mcp", label: label.trim() || "Claude Desktop" },
        { headers: { Authorization: `Bearer ${getToken()}` } });
      setNewKey(r.data?.key || "");
      setLabel("");
      await load();
    } catch (e) {
      toast({ message: cleanErr(e, "Failed to generate key."), kind: "error", duration: 3500 });
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (keyId) => {
    try {
      await api.post(`/admin/gateway/keys/${keyId}/revoke?scope=mcp`, {}, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      await load();
    } catch (e) {
      toast({ message: cleanErr(e, "Revoke failed."), kind: "error", duration: 3000 });
    }
  };

  const regenerate = async (keyId) => {
    setBusy(true);
    try {
      const r = await api.post(`/admin/gateway/keys/${keyId}/regenerate?scope=mcp`, {}, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setNewKey(r.data?.key || "");
      await load();
    } catch (e) {
      toast({ message: cleanErr(e, "Regenerate failed."), kind: "error", duration: 3000 });
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    setTestBusy(true);
    setTestResult(null);
    try {
      const r = await api.post("/admin/gateway/test/mcp", {}, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setTestResult({ ok: true, reply: r.data?.result?.reply || "(empty reply)" });
    } catch (e) {
      setTestResult({ ok: false, reply: cleanErr(e, "Test failed.") });
    } finally {
      setTestBusy(false);
    }
  };

  const copy = (text) => {
    navigator.clipboard.writeText(text);
    toast({ message: "Copied.", kind: "success", duration: 1500 });
  };

  return (
    <div style={{ ...hintBox, marginTop: 40, borderColor: "rgba(255,180,84,0.35)" }} data-testid="ora-mcp-card">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <h3 style={{ ...sectionLabel, margin: 0 }}>ORA Chat — MCP</h3>
        <span style={adminOnlyBadge}>ADMIN ONLY</span>
      </div>
      <p style={{ color: "var(--text-dim)", fontSize: 13, margin: "0 0 16px", lineHeight: 1.6 }}>
        Lets Claude Desktop call ORA Chat (AUREM&apos;s internal assistant) as a tool
        inside any conversation: <code style={codeChip}>ask_ora_chat</code>,{" "}
        <code style={codeChip}>list_ora_conversations</code>,{" "}
        <code style={codeChip}>get_ora_conversation</code>. Bearer-key auth
        (OAuth 2.1 not built yet). Server-enforced admin-only.
      </p>

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--text-faint)", textTransform: "uppercase" }}>MCP URL</span>
        <code data-testid="ora-mcp-url" style={{ ...codeChip, fontSize: 12 }}>{mcpUrl}</code>
        <button style={btnGhost} onClick={() => copy(mcpUrl)} data-testid="ora-mcp-copy-url">Copy</button>
      </div>

      {newKey && (
        <div data-testid="ora-mcp-new-key-banner" style={revealBanner}>
          <div style={{ fontSize: 11, color: "#ffb454", letterSpacing: "0.12em",
                        textTransform: "uppercase", marginBottom: 8 }}>
            ⚠ Copy this now — it won&apos;t be shown again
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <code data-testid="ora-mcp-new-key-value" style={keyDisplay}>{newKey}</code>
            <button style={btnGhost} onClick={() => copy(newKey)} data-testid="ora-mcp-copy-key">Copy</button>
            <button style={btnGhost} onClick={() => setNewKey(null)} data-testid="ora-mcp-dismiss-key">
              I&apos;ve saved it
            </button>
          </div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 10, lineHeight: 1.6 }}>
            Claude Desktop → Settings → Connectors → Add custom connector →
            paste the MCP URL above → Advanced settings → Request headers:
            add <code style={codeChip}>Authorization</code> ={" "}
            <code style={codeChip}>Bearer {newKey.slice(0, 18)}…</code>
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        <input
          data-testid="ora-mcp-label-input"
          placeholder="Key label (e.g. My laptop)"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          style={{ ...codeChip, padding: "8px 12px", minWidth: 200, background: "var(--bg)" }}
        />
        <button data-testid="ora-mcp-create-key" onClick={create} disabled={busy} style={btnPrimary(busy)}>
          {busy ? "Generating…" : "+ Generate MCP key"}
        </button>
        <button data-testid="ora-mcp-test-btn" onClick={runTest} disabled={testBusy} style={btnGhost}>
          {testBusy ? "Testing…" : "Test (ask_ora_chat: ping)"}
        </button>
      </div>

      {testResult && (
        <div data-testid="ora-mcp-test-result"
             style={testResult.ok ? { ...revealBanner, background: "rgba(109,212,161,0.06)", borderColor: "rgba(109,212,161,0.3)" } : errBanner}>
          {testResult.reply}
        </div>
      )}

      {loading ? (
        <div style={emptyState}>Loading…</div>
      ) : keys.length === 0 ? (
        <div data-testid="ora-mcp-keys-empty" style={emptyState}>No MCP keys yet.</div>
      ) : (
        <div style={tableWrap}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-faint)",
                           textTransform: "uppercase", fontSize: 10, letterSpacing: "0.1em" }}>
                <th style={th}>Key / Label</th>
                <th style={th}>Last used</th>
                <th style={th}>Rate limit</th>
                <th style={th}>Spend this month</th>
                <th style={th}>Status</th>
                <th style={{ ...th, textAlign: "right" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.key_id} data-testid={`ora-mcp-key-row-${k.key_id}`}
                    style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={td}>
                    <code style={codeChip}>{k.key_prefix}…</code>
                    <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-faint)" }}>{k.label}</span>
                  </td>
                  <td style={td}>{timeSince(k.last_used_at)}</td>
                  <td style={td}>{k.rate_limit_per_min}/min</td>
                  <td style={td}>
                    ${k.current_month_spend_usd?.toFixed(2)} / ${k.spend_cap_monthly_usd?.toFixed(0)}
                    {k.over_alert_threshold && (
                      <span style={{ ...badgeRevoked, color: "#ffb454", marginLeft: 6,
                                     borderColor: "rgba(255,180,84,0.4)", background: "rgba(255,180,84,0.1)" }}>
                        ≥80%
                      </span>
                    )}
                  </td>
                  <td style={td}>
                    <span style={k.revoked ? badgeRevoked : badgeOk}>{k.revoked ? "Revoked" : "Active"}</span>
                  </td>
                  <td style={{ ...td, textAlign: "right" }}>
                    {!k.revoked && (
                      <>
                        <button data-testid={`ora-mcp-regen-${k.key_id}`} onClick={() => regenerate(k.key_id)}
                                style={{ ...btnGhost, marginRight: 6, padding: "6px 10px", fontSize: 11 }}>
                          Regenerate
                        </button>
                        <button data-testid={`ora-mcp-revoke-${k.key_id}`} onClick={() => revoke(k.key_id)} style={btnDanger}>
                          Revoke
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
const adminOnlyBadge = {
  fontSize: 10, letterSpacing: "0.1em", padding: "2px 8px", borderRadius: 10,
  color: "#ffb454", background: "rgba(255,180,84,0.1)", border: "1px solid rgba(255,180,84,0.35)",
};

// ── Styles ──────────────────────────────────────────────────────────
const codeChip = {
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 11.5,
  background: "var(--card-2)",
  padding: "2px 6px",
  borderRadius: 4,
  border: "1px solid var(--border)",
  color: "var(--text)",
};
const btnPrimary = (disabled) => ({
  padding: "10px 18px", fontSize: 13, fontWeight: 600,
  background: disabled ? "var(--card-2)" : "var(--accent)",
  color: disabled ? "var(--text-faint)" : "#0a0a0a",
  border: "none", borderRadius: 6,
  cursor: disabled ? "not-allowed" : "pointer",
  fontFamily: "inherit",
});
const btnGhost = {
  padding: "8px 14px", fontSize: 12,
  background: "transparent",
  color: "var(--text-dim)",
  border: "1px solid var(--border)", borderRadius: 6,
  cursor: "pointer", fontFamily: "inherit",
};
const btnDanger = {
  padding: "6px 12px", fontSize: 11,
  background: "transparent",
  color: "#ff6b6b",
  border: "1px solid rgba(255,107,107,0.3)",
  borderRadius: 4, cursor: "pointer", fontFamily: "inherit",
};
const errBanner = {
  padding: 12, marginBottom: 16,
  background: "rgba(255,107,107,0.08)",
  border: "1px solid rgba(255,107,107,0.3)",
  borderRadius: 6, color: "#ff6b6b", fontSize: 13,
};
const revealBanner = {
  padding: 18, marginBottom: 24,
  background: "rgba(255,180,84,0.06)",
  border: "1px solid rgba(255,180,84,0.3)",
  borderRadius: 8,
};
const keyDisplay = {
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 12, padding: "8px 12px",
  background: "var(--bg)", border: "1px solid var(--border)",
  borderRadius: 4, color: "var(--accent)",
  wordBreak: "break-all", flex: 1, minWidth: 0,
};
const sectionLabel = {
  fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase",
  color: "var(--text-faint)", margin: "0 0 12px",
};
const emptyState = {
  padding: 32, textAlign: "center",
  color: "var(--text-faint)", fontSize: 13,
  background: "var(--card)",
  border: "1px dashed var(--border)", borderRadius: 8,
};
const tableWrap = {
  border: "1px solid var(--border)", borderRadius: 8,
  overflow: "hidden", background: "var(--card)",
};
const th = { padding: "12px 14px", textAlign: "left", fontWeight: 500 };
const td = { padding: "12px 14px", color: "var(--text)" };
const badgeOk = {
  display: "inline-block", padding: "2px 8px", borderRadius: 10,
  fontSize: 11, color: "#6dd4a1",
  background: "rgba(109,212,161,0.10)",
  border: "1px solid rgba(109,212,161,0.3)",
};
const badgeRevoked = {
  display: "inline-block", padding: "2px 8px", borderRadius: 10,
  fontSize: 11, color: "var(--text-faint)",
  background: "rgba(136,141,153,0.10)",
  border: "1px solid var(--border)",
};
const hintBox = {
  marginTop: 32, padding: 18,
  background: "var(--card)", border: "1px solid var(--border)",
  borderRadius: 8,
};
const preBlock = {
  margin: 0, padding: 14, fontSize: 11.5, lineHeight: 1.5,
  fontFamily: "'JetBrains Mono', monospace",
  background: "var(--bg)", border: "1px solid var(--border)",
  borderRadius: 6, color: "var(--text-dim)",
  overflow: "auto",
};
