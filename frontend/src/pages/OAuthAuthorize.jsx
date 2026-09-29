/**
 * pages/OAuthAuthorize.jsx — 2026-09-29 OAuth 2.1 consent screen for
 * the ORA Chat MCP server.
 *
 * Reached via GET /api/aurem-dev/oauth/authorize (routers/oauth.py)
 * redirecting the browser here with `?consent_id=...` after validating
 * the OAuth request (client_id, redirect_uri, PKCE challenge, resource).
 *
 * Wrapped in <AdminRoute> in App.jsx — that already handles "not
 * logged in -> /login?next=..." and "logged in but not admin ->
 * /dashboard", using the SAME JWT (+ 2FA) system as the rest of the
 * admin panel. This component can assume it's rendering for a real
 * admin by the time it mounts.
 */
import React, { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, getToken } from "../lib/api";
import { cleanErr } from "../lib/cleanErr";

export default function OAuthAuthorize() {
  const [searchParams] = useSearchParams();
  const consentId = searchParams.get("consent_id") || "";
  const [pending, setPending] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!consentId) {
      setErr("Missing authorization request. Ask Claude to reconnect.");
      setLoading(false);
      return;
    }
    api.get(`/oauth/pending/${consentId}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then((r) => setPending(r.data))
      .catch((e) => setErr(cleanErr(e, "This authorization request has expired.")))
      .finally(() => setLoading(false));
  }, [consentId]);

  const decide = async (decision) => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.post("/oauth/authorize/approve",
        { consent_id: consentId, decision },
        { headers: { Authorization: `Bearer ${getToken()}` } });
      window.location.href = r.data.redirect_url;
    } catch (e) {
      setErr(cleanErr(e, "Could not complete authorization."));
      setBusy(false);
    }
  };

  return (
    <div style={wrap} data-testid="oauth-authorize-page">
      <div style={card}>
        <div style={badge}>ADMIN ONLY · OAUTH 2.1</div>
        <h1 style={heading}>Connect to ORA Chat</h1>

        {loading && <p style={dim} data-testid="oauth-authorize-loading">Loading request…</p>}

        {!loading && err && (
          <p style={errText} data-testid="oauth-authorize-error">{err}</p>
        )}

        {!loading && !err && pending && (
          <>
            <p style={dim}>
              <strong style={{ color: "var(--text)" }}>{pending.client_name}</strong>{" "}
              wants to use ORA Chat (AUREM&apos;s internal admin assistant) on your behalf.
            </p>
            <p style={dim}>
              Requested access: <code style={codeChip}>{pending.scope}</code>
            </p>
            <p style={{ ...dim, fontSize: 12 }}>
              You are signing in as an ORA admin. Only admin accounts can
              authorize this connection.
            </p>
            <div style={{ display: "flex", gap: 10, marginTop: 24 }}>
              <button
                data-testid="oauth-authorize-allow"
                onClick={() => decide("allow")}
                disabled={busy}
                style={btnPrimary(busy)}
              >
                {busy ? "Connecting…" : "Allow"}
              </button>
              <button
                data-testid="oauth-authorize-deny"
                onClick={() => decide("deny")}
                disabled={busy}
                style={btnGhost}
              >
                Deny
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const wrap = {
  minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
  background: "var(--bg)", padding: 24,
};
const card = {
  maxWidth: 440, width: "100%", padding: 32,
  background: "var(--card)", border: "1px solid var(--border)", borderRadius: 12,
};
const badge = {
  display: "inline-block", fontSize: 10, letterSpacing: "0.12em", padding: "3px 9px",
  borderRadius: 10, color: "#ffb454", background: "rgba(255,180,84,0.1)",
  border: "1px solid rgba(255,180,84,0.35)", marginBottom: 16,
};
const heading = { fontSize: 22, fontWeight: 700, margin: "0 0 16px", color: "var(--text)" };
const dim = { color: "var(--text-dim)", fontSize: 14, lineHeight: 1.6, margin: "0 0 10px" };
const errText = { color: "#ff6b6b", fontSize: 14, lineHeight: 1.6 };
const codeChip = {
  fontFamily: "'JetBrains Mono', monospace", fontSize: 12.5,
  background: "var(--card-2)", padding: "2px 6px", borderRadius: 4,
  border: "1px solid var(--border)", color: "var(--text)",
};
const btnPrimary = (disabled) => ({
  padding: "10px 22px", fontSize: 14, fontWeight: 600,
  background: disabled ? "var(--card-2)" : "var(--accent)",
  color: disabled ? "var(--text-faint)" : "#0a0a0a",
  border: "none", borderRadius: 6, cursor: disabled ? "not-allowed" : "pointer",
  fontFamily: "inherit",
});
const btnGhost = {
  padding: "10px 22px", fontSize: 14, background: "transparent",
  color: "var(--text-dim)", border: "1px solid var(--border)", borderRadius: 6,
  cursor: "pointer", fontFamily: "inherit",
};
