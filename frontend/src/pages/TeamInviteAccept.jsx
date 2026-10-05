/**
 * pages/TeamInviteAccept.jsx — Admin Team S3 (AT2) invite acceptance.
 *
 * Route: /team-invite/accept?token=... — requires the person to
 * already be logged into their own AUREM account (PrivateRoute, not
 * AdminRoute — they aren't an admin yet). Accepting never signs them
 * in and never returns a new session; it just grants their existing
 * account Admin Team access. A SEPARATE feature from the existing
 * workspace Team's /team/accept-invite (AcceptInvite.jsx) — untouched.
 */
import React, { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { UserPlus, ShieldAlert, CheckCircle2 } from "lucide-react";
import { api, getUser } from "../lib/api";

export default function TeamInviteAccept() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") || "";
  const me = getUser();

  const [status, setStatus] = useState("idle"); // idle | busy | done | error
  const [error, setError] = useState("");
  const [role, setRole] = useState("");

  async function accept() {
    setStatus("busy"); setError("");
    try {
      const r = await api.post("/team-invite/accept", { token });
      setRole(r.data.role);
      setStatus("done");
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't accept this invite.");
      setStatus("error");
    }
  }

  const shell = (children) => (
    <div data-testid="team-invite-accept-page" style={{
      minHeight: "100vh", background: "var(--bg, #0A0A0A)", color: "var(--text, #F5F5F5)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
    }}>
      <div style={{ maxWidth: 400, width: "100%", textAlign: "center" }}>{children}</div>
    </div>
  );

  if (!token) {
    return shell(
      <div data-testid="team-invite-accept-missing-token">
        <ShieldAlert size={32} style={{ marginBottom: 12, opacity: 0.7 }} />
        <h2 style={{ fontSize: 18, margin: "0 0 8px" }}>Missing invite link</h2>
        <p style={{ fontSize: 13, color: "var(--text-dim, #999)" }}>This link is incomplete — ask the Owner to send it again.</p>
      </div>
    );
  }

  if (status === "done") {
    return shell(
      <div data-testid="team-invite-accept-success">
        <CheckCircle2 size={32} style={{ marginBottom: 12, color: "#22c55e" }} />
        <h2 style={{ fontSize: 18, margin: "0 0 8px" }}>You're in!</h2>
        <p style={{ fontSize: 13, color: "var(--text-dim, #999)", marginBottom: 16 }}>
          You're now a {role} on the Admin Team, signed in as {me?.email}.
        </p>
        <button className="btn-primary" data-testid="team-invite-accept-go-dashboard-btn"
                onClick={() => navigate("/dashboard", { replace: true })}>
          Go to dashboard
        </button>
      </div>
    );
  }

  return shell(
    <div data-testid="team-invite-accept-prompt">
      <UserPlus size={28} style={{ marginBottom: 10, opacity: 0.85 }} />
      <h2 style={{ fontSize: 18, margin: "0 0 4px" }}>Join the Admin Team</h2>
      <p style={{ fontSize: 13, color: "var(--text-dim, #999)", marginBottom: 16 }}>
        You're signed in as <strong>{me?.email}</strong>. Accepting will grant
        this account Admin Team access — it will not create a new login.
      </p>
      {error && <div data-testid="team-invite-accept-error" style={{ fontSize: 12, color: "#ff6b6b", marginBottom: 12 }}>{error}</div>}
      <button className="btn-primary" disabled={status === "busy"}
              data-testid="team-invite-accept-submit-btn" onClick={accept}>
        {status === "busy" ? "Accepting…" : "Accept invite"}
      </button>
    </div>
  );
}
