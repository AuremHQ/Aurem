/**
 * AcceptInvite.jsx — 2026-02 · Phase 1 (Team Roles & Permissions)
 *
 * Public page (no auth) at /team/accept-invite?token=... — the
 * invited person sets their own password here, which creates their
 * own dev_users login and links it to the inviting account via
 * team_members.user_id (see routers/team_management.py::accept_invite).
 */
import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { UserPlus, ShieldAlert } from "lucide-react";
import { api, setToken, setUser } from "../lib/api";

export default function AcceptInvite() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") || "";

  const [preview, setPreview] = useState(null); // null=loading, {}=result
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!token) { setPreview({ valid: false }); return; }
    api.get(`/team/invite/${token}`).then((r) => setPreview(r.data))
       .catch(() => setPreview({ valid: false }));
  }, [token]);

  async function submit(e) {
    e.preventDefault();
    if (password.length < 6) { setError("Password must be at least 6 characters."); return; }
    if (password !== confirm) { setError("Passwords don't match."); return; }
    setBusy(true); setError(null);
    try {
      const r = await api.post("/team/accept-invite", { token, password });
      setToken(r.data.token);
      setUser(r.data.user);
      try { localStorage.setItem("aurem_fresh_login", "1"); } catch { /* ignore */ }
      navigate("/dashboard", { replace: true });
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't accept this invite.");
    } finally {
      setBusy(false);
    }
  }

  const shell = (children) => (
    <div data-testid="accept-invite-page" style={{
      minHeight: "100vh", background: "var(--bg, #0A0A0A)", color: "var(--text, #F5F5F5)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
    }}>
      <div style={{ maxWidth: 380, width: "100%" }}>{children}</div>
    </div>
  );

  if (preview === null) return shell(<p data-testid="accept-invite-loading">Loading invite…</p>);

  if (!preview.valid) {
    return shell(
      <div style={{ textAlign: "center" }} data-testid="accept-invite-invalid">
        <ShieldAlert size={32} style={{ marginBottom: 12, opacity: 0.7 }} />
        <h2 style={{ fontSize: 18, margin: "0 0 8px" }}>Invite link is invalid or expired</h2>
        <p style={{ fontSize: 13, color: "var(--text-dim, #999)" }}>
          Ask whoever invited you to resend the invite.
        </p>
      </div>
    );
  }

  return shell(
    <form onSubmit={submit} data-testid="accept-invite-form" style={{ display: "grid", gap: 14 }}>
      <div style={{ textAlign: "center", marginBottom: 6 }}>
        <UserPlus size={28} style={{ marginBottom: 10, opacity: 0.85 }} />
        <h2 style={{ fontSize: 18, margin: "0 0 4px" }}>Join the team on AUREM</h2>
        <p style={{ fontSize: 13, color: "var(--text-dim, #999)" }} data-testid="accept-invite-role">
          {preview.email} — invited as {preview.role_label}
        </p>
      </div>
      <label>
        <span className="label-mini">Set your password</span>
        <input className="input" type="password" required minLength={6} autoFocus
               data-testid="accept-invite-password-input"
               value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      <label>
        <span className="label-mini">Confirm password</span>
        <input className="input" type="password" required minLength={6}
               data-testid="accept-invite-confirm-input"
               value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </label>
      {error && <div data-testid="accept-invite-error" style={{ fontSize: 12, color: "#ff6b6b" }}>{error}</div>}
      <button type="submit" className="btn-primary" disabled={busy}
              data-testid="accept-invite-submit-btn">
        {busy ? "Setting up…" : "Create account & join"}
      </button>
    </form>
  );
}
