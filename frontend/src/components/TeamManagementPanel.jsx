/**
 * TeamManagementPanel.jsx — 2026-02 · Phase 1 (Team Roles & Permissions)
 *
 * Settings → Team tab. Invite by email, see pending/active/revoked
 * members, edit each member's per-resource access in a matrix grid,
 * revoke access, resend an invite. This is UX only — every action
 * here is re-checked server-side by `require_permission` in
 * backend/services/team_permissions.py; hiding a button here never
 * substitutes for that.
 */
import React, { useEffect, useState, useCallback } from "react";
import { UserPlus, Trash2, RotateCcw, Shield, Loader2 } from "lucide-react";
import { api } from "../lib/api";

const LEVELS = ["none", "read", "write", "read_write"];
const LEVEL_LABEL = { none: "No access", read: "Read only", write: "Write only", read_write: "Full" };

export default function TeamManagementPanel() {
  const [me, setMe] = useState(null);
  const [resources, setResources] = useState({});
  const [presets, setPresets] = useState({});
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("viewer");
  const [inviting, setInviting] = useState(false);
  const [expanded, setExpanded] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [meRes, resRes, memRes] = await Promise.all([
        api.get("/team/me"),
        api.get("/team/resources"),
        api.get("/team/members").catch(() => ({ data: { members: [] } })),
      ]);
      setMe(meRes.data);
      setResources(resRes.data.resources || {});
      setPresets(resRes.data.role_presets || {});
      setMembers(memRes.data.members || []);
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't load team settings.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const canManage = me?.is_owner || me?.permissions?.team_management === "write" ||
                     me?.permissions?.team_management === "read_write";

  async function invite(e) {
    e.preventDefault();
    if (!inviteEmail.trim() || inviting) return;
    setInviting(true);
    setError(null);
    try {
      const r = await api.post("/team/invite", { email: inviteEmail.trim(), role_label: inviteRole });
      if (r.data.email_sent === false) {
        setError(`Invite created, but the email couldn't be sent (${r.data.email_error || "unknown error"}). Share the accept-invite link manually.`);
      }
      setInviteEmail("");
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't send invite.");
    } finally {
      setInviting(false);
    }
  }

  async function setPermission(memberId, resource, level) {
    const member = members.find((m) => m.member_id === memberId);
    const next = { ...(member?.permissions || {}), [resource]: level };
    setMembers((prev) => prev.map((m) => m.member_id === memberId ? { ...m, permissions: next } : m));
    try {
      await api.put(`/team/members/${memberId}/permissions`, { permissions: { [resource]: level } });
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't update permission.");
      await load();
    }
  }

  async function revoke(memberId) {
    if (!window.confirm("Revoke this person's access? They will immediately lose access to your workspace.")) return;
    try {
      await api.post(`/team/members/${memberId}/revoke`);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't revoke.");
    }
  }

  async function resend(memberId) {
    try {
      const r = await api.post(`/team/members/${memberId}/resend-invite`);
      if (r.data.email_sent === false) {
        setError(`Couldn't resend the email (${r.data.email_error || "unknown error"}).`);
      }
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't resend invite.");
    }
  }

  if (loading) {
    return (
      <div data-testid="team-panel-loading" style={{ padding: 24, color: "var(--muted)", fontSize: 13, display: "flex", alignItems: "center", gap: 8 }}>
        <Loader2 size={16} className="animate-spin" /> Loading team settings…
      </div>
    );
  }

  if (!canManage) {
    return (
      <div data-testid="team-panel-no-access" className="card" style={{ padding: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text)" }}>
          <Shield size={16} />
          <span style={{ fontSize: 14 }}>Only the account owner or an admin can manage the team.</span>
        </div>
      </div>
    );
  }

  return (
    <div data-testid="team-management-panel" style={{ display: "grid", gap: 20 }}>
      {error && (
        <div data-testid="team-panel-error" className="card"
             style={{ borderColor: "var(--danger)", color: "var(--danger)", fontSize: 13, padding: 12 }}>
          {error}
        </div>
      )}

      <section className="card">
        <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
          <UserPlus size={14} /> Invite a team member
        </h3>
        <form onSubmit={invite} style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ flex: "1 1 220px" }}>
            <span className="label-mini">Email</span>
            <input className="input" type="email" required data-testid="team-invite-email-input"
                   value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)}
                   placeholder="teammate@company.com" />
          </label>
          <label>
            <span className="label-mini">Starting role</span>
            <select className="input" data-testid="team-invite-role-select"
                    value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
              {Object.keys(presets).map((r) => (
                <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>
              ))}
            </select>
          </label>
          <button type="submit" className="btn-primary" disabled={inviting}
                  data-testid="team-invite-submit-btn">
            {inviting ? "Sending…" : "Send invite"}
          </button>
        </form>
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 10, marginBottom: 0 }}>
          New invites start with the preset's access — you can fine-tune any resource
          per person below. They see nothing beyond what's granted.
        </p>
      </section>

      <section className="card">
        <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14 }}>
          Team ({members.length})
        </h3>
        {members.length === 0 ? (
          <div data-testid="team-members-empty" style={{ fontSize: 13, color: "var(--muted)" }}>
            No one invited yet.
          </div>
        ) : (
          <div style={{ display: "grid", gap: 10 }} data-testid="team-members-list">
            {members.map((m) => (
              <div key={m.member_id} className="card" style={{ padding: 14 }}
                   data-testid={`team-member-row-${m.member_id}`}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontSize: 14, color: "var(--text)", fontWeight: 600 }}>{m.email}</div>
                    <div style={{ fontSize: 12, color: "var(--muted)" }}>
                      <StatusBadge status={m.status} /> · {m.role_label}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="btn-ghost" data-testid={`team-member-matrix-toggle-${m.member_id}`}
                            onClick={() => setExpanded(expanded === m.member_id ? null : m.member_id)}>
                      {expanded === m.member_id ? "Hide access" : "Edit access"}
                    </button>
                    {m.status === "pending" && (
                      <button className="btn-ghost" data-testid={`team-member-resend-${m.member_id}`}
                              onClick={() => resend(m.member_id)} title="Resend invite">
                        <RotateCcw size={14} />
                      </button>
                    )}
                    {m.status !== "revoked" && (
                      <button className="btn-danger" data-testid={`team-member-revoke-${m.member_id}`}
                              onClick={() => revoke(m.member_id)} title="Revoke access">
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </div>
                {expanded === m.member_id && (
                  <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 14 }}
                       data-testid={`team-member-matrix-${m.member_id}`}>
                    <div style={{ display: "grid", gap: 8 }}>
                      {Object.entries(resources).map(([key, label]) => (
                        <div key={key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
                          <span style={{ fontSize: 13, color: "var(--text)" }}>{label}</span>
                          <select className="input" style={{ width: 160 }}
                                  data-testid={`team-member-perm-${m.member_id}-${key}`}
                                  value={m.permissions?.[key] || "none"}
                                  disabled={m.status === "revoked"}
                                  onChange={(e) => setPermission(m.member_id, key, e.target.value)}>
                            {LEVELS.map((lv) => (
                              <option key={lv} value={lv}>{LEVEL_LABEL[lv]}</option>
                            ))}
                          </select>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function StatusBadge({ status }) {
  const color = status === "active" ? "#3ddc84" : status === "pending" ? "var(--accent)" : "var(--danger)";
  return <span data-testid={`team-status-${status}`} style={{ color, fontWeight: 600 }}>{status}</span>;
}
