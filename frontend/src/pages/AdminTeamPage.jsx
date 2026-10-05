/**
 * pages/AdminTeamPage.jsx — Admin Team S3 (AT2): Team screen.
 *
 * Owner-only invite/manage UI for `admin_team_members` /
 * `admin_team_invites` (GET/POST/PATCH /admin/team/*). A separate
 * feature from the existing workspace Team page — different
 * collection, different purpose (who gets into the Admin panel, at
 * what level, not project collaborators).
 *
 * D8 — the invite link is shown exactly once right after creating (or
 * regenerating via "Copy link"). It is never fetchable again — close
 * the dialog and it's gone, by design; regenerate a new one instead.
 */
import React, { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";

const LEVELS = ["off", "view", "work", "manage"];

function CreateInviteDialog({ screens, onCreated, onClose }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e) {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy(true); setErr("");
    try {
      const r = await api.post("/admin/team/invites", { email: email.trim(), role: "developer" });
      onCreated(r.data);
    } catch (e2) {
      setErr(e2?.response?.data?.detail || e2.message || "Failed to create invite");
    } finally { setBusy(false); }
  }

  return (
    <div data-testid="admin-team-invite-dialog" style={overlayStyle}>
      <form onSubmit={submit} style={dialogStyle}>
        <h3 style={{ margin: "0 0 10px", fontSize: 14, color: "var(--text)" }}>Invite a developer</h3>
        <input
          data-testid="admin-team-invite-email-input"
          type="email"
          autoFocus
          placeholder="teammate@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={inputStyle}
        />
        {err && <div data-testid="admin-team-invite-error" style={{ color: "#ef4444", fontSize: 11, marginTop: 6 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14 }}>
          <button type="button" onClick={onClose} className="btn-ghost" data-testid="admin-team-invite-cancel-btn" style={{ fontSize: 12 }}>Cancel</button>
          <button type="submit" disabled={busy} data-testid="admin-team-invite-submit-btn" className="btn-primary" style={{ fontSize: 12 }}>
            {busy ? "Creating…" : "Create invite"}
          </button>
        </div>
      </form>
    </div>
  );
}

function TokenRevealDialog({ token, email, onClose }) {
  const [copied, setCopied] = useState(false);
  const link = `${window.location.origin}/team-invite/accept?token=${encodeURIComponent(token)}`;

  function copyLink() {
    navigator.clipboard?.writeText(link);
    setCopied(true);
    toast("Invite link copied");
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div data-testid="admin-team-token-reveal-dialog" style={overlayStyle}>
      <div style={dialogStyle}>
        <h3 style={{ margin: "0 0 6px", fontSize: 14, color: "var(--text)" }}>Invite link for {email}</h3>
        <p style={{ fontSize: 11, color: "var(--text-faint)", margin: "0 0 10px" }}>
          This link is shown once. Copy it now — you can always generate a fresh one later, which invalidates this one.
        </p>
        <div style={{ display: "flex", gap: 6 }}>
          <input readOnly value={link} data-testid="admin-team-token-reveal-input" style={{ ...inputStyle, flex: 1, fontFamily: "monospace", fontSize: 11 }} onFocus={(e) => e.target.select()} />
          <button onClick={copyLink} data-testid="admin-team-token-copy-btn" className="btn-primary" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
          <button onClick={onClose} className="btn-ghost" data-testid="admin-team-token-reveal-close-btn" style={{ fontSize: 12 }}>Done</button>
        </div>
      </div>
    </div>
  );
}

function OverridesDialog({ member, screens, onSaved, onClose }) {
  const [overrides, setOverrides] = useState(member.overrides || {});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  function setLevel(screenKey, level) {
    setOverrides((prev) => {
      const next = { ...prev };
      if (level === "") delete next[screenKey];
      else next[screenKey] = level;
      return next;
    });
  }

  async function save() {
    setBusy(true); setErr("");
    try {
      await api.patch(`/admin/team/members/${member.user_id}/overrides`, { overrides });
      onSaved(overrides);
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message || "Failed to save overrides");
    } finally { setBusy(false); }
  }

  return (
    <div data-testid="admin-team-overrides-dialog" style={overlayStyle}>
      <div style={{ ...dialogStyle, maxHeight: "70vh", overflowY: "auto", width: 460 }}>
        <h3 style={{ margin: "0 0 10px", fontSize: 14, color: "var(--text)" }}>Screen overrides — {member.email}</h3>
        {screens.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--text-faint)" }}>No screens found in the registry yet.</div>
        )}
        {screens.map((key) => (
          <div key={key} data-testid={`admin-team-override-row-${key}`} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)", fontFamily: "monospace" }}>{key}</span>
            <select
              value={overrides[key] || ""}
              onChange={(e) => setLevel(key, e.target.value)}
              data-testid={`admin-team-override-select-${key}`}
              style={{ fontSize: 11, background: "var(--bg-elev)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 4, padding: "2px 6px" }}
            >
              <option value="">role default</option>
              {LEVELS.map((lvl) => <option key={lvl} value={lvl}>{lvl}</option>)}
            </select>
          </div>
        ))}
        {err && <div style={{ color: "#ef4444", fontSize: 11, marginTop: 8 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14 }}>
          <button onClick={onClose} className="btn-ghost" data-testid="admin-team-overrides-cancel-btn" style={{ fontSize: 12 }}>Cancel</button>
          <button onClick={save} disabled={busy} data-testid="admin-team-overrides-save-btn" className="btn-primary" style={{ fontSize: 12 }}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function AdminTeamPage() {
  const [members, setMembers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [screens, setScreens] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [showInvite, setShowInvite] = useState(false);
  const [revealToken, setRevealToken] = useState(null); // { token, email }
  const [overridesFor, setOverridesFor] = useState(null); // member row

  const load = useCallback(async () => {
    setLoading(true); setErr("");
    try {
      const [m, i, s] = await Promise.all([
        api.get("/admin/team/members"),
        api.get("/admin/team/invites"),
        api.get("/admin/team/screens"),
      ]);
      setMembers(m.data?.members || []);
      setInvites(i.data?.invites || []);
      setScreens(s.data?.screens || []);
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message || "Failed to load the Admin Team screen");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function cancelInvite(inviteId) {
    try {
      await api.post(`/admin/team/invites/${inviteId}/cancel`);
      toast("Invite cancelled");
      load();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Failed to cancel invite", kind: "error" });
    }
  }

  async function copyLink(invite) {
    try {
      const r = await api.post(`/admin/team/invites/${invite.invite_id}/copy-link`);
      setRevealToken({ token: r.data.token, email: invite.email });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Failed to regenerate link", kind: "error" });
    }
  }

  async function suspend(userId) {
    try { await api.post(`/admin/team/members/${userId}/suspend`); toast("Member suspended"); load(); }
    catch (e) { toast({ message: e?.response?.data?.detail || "Failed to suspend", kind: "error" }); }
  }
  async function resume(userId) {
    try { await api.post(`/admin/team/members/${userId}/resume`); toast("Member resumed"); load(); }
    catch (e) { toast({ message: e?.response?.data?.detail || "Failed to resume", kind: "error" }); }
  }
  async function remove(userId) {
    if (!window.confirm("Remove this team member? They will lose all Admin Team access immediately.")) return;
    try { await api.post(`/admin/team/members/${userId}/remove`); toast("Member removed"); load(); }
    catch (e) { toast({ message: e?.response?.data?.detail || "Failed to remove", kind: "error" }); }
  }

  return (
    <div data-testid="admin-team-page" style={{ padding: 24 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 16, color: "var(--text)" }}>Admin Team</h2>
        <button onClick={() => setShowInvite(true)} data-testid="admin-team-invite-open-btn" className="btn-primary" style={{ fontSize: 12 }}>
          + Invite developer
        </button>
      </div>

      {loading && <div style={{ color: "var(--text-faint)", fontSize: 12 }}>Loading…</div>}
      {err && <div data-testid="admin-team-page-error" style={{ color: "#ef4444", fontSize: 12 }}>{err}</div>}

      {!loading && !err && (
        <>
          <section style={{ marginBottom: 28 }}>
            <h3 style={sectionTitle}>Members</h3>
            {members.length === 0 ? (
              <div style={{ color: "var(--text-faint)", fontSize: 12 }}>No team members yet — invite one above.</div>
            ) : (
              <table data-testid="admin-team-members-table" style={tableStyle}>
                <thead>
                  <tr style={theadRowStyle}><th style={th}>Email</th><th style={th}>Role</th><th style={th}>Status</th><th style={th}>Overrides</th><th style={th}></th></tr>
                </thead>
                <tbody>
                  {members.map((m) => (
                    <tr key={m.user_id} data-testid={`admin-team-member-row-${m.user_id}`} style={rowStyle}>
                      <td style={td}>{m.email}</td>
                      <td style={td}>{m.role}</td>
                      <td style={td}>
                        <span style={{ color: m.status === "active" ? "#22c55e" : "#f59e0b" }}>{m.status}</span>
                      </td>
                      <td style={td}>{Object.keys(m.overrides || {}).length || "—"}</td>
                      <td style={{ ...td, display: "flex", gap: 6 }}>
                        <button onClick={() => setOverridesFor(m)} data-testid={`admin-team-member-overrides-btn-${m.user_id}`} className="btn-ghost" style={{ fontSize: 11 }}>Overrides</button>
                        {m.status === "active" ? (
                          <button onClick={() => suspend(m.user_id)} data-testid={`admin-team-member-suspend-btn-${m.user_id}`} className="btn-ghost" style={{ fontSize: 11 }}>Suspend</button>
                        ) : (
                          <button onClick={() => resume(m.user_id)} data-testid={`admin-team-member-resume-btn-${m.user_id}`} className="btn-ghost" style={{ fontSize: 11 }}>Resume</button>
                        )}
                        <button onClick={() => remove(m.user_id)} data-testid={`admin-team-member-remove-btn-${m.user_id}`} className="btn-ghost" style={{ fontSize: 11, color: "#ef4444" }}>Remove</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section>
            <h3 style={sectionTitle}>Pending invites</h3>
            {invites.length === 0 ? (
              <div style={{ color: "var(--text-faint)", fontSize: 12 }}>No pending invites.</div>
            ) : (
              <table data-testid="admin-team-invites-table" style={tableStyle}>
                <thead>
                  <tr style={theadRowStyle}><th style={th}>Email</th><th style={th}>Role</th><th style={th}>Invited</th><th style={th}></th></tr>
                </thead>
                <tbody>
                  {invites.map((inv) => (
                    <tr key={inv.invite_id} data-testid={`admin-team-invite-row-${inv.invite_id}`} style={rowStyle}>
                      <td style={td}>{inv.email}</td>
                      <td style={td}>{inv.role}</td>
                      <td style={td}>{(inv.invited_at || "").toString().slice(0, 19).replace("T", " ")}</td>
                      <td style={{ ...td, display: "flex", gap: 6 }}>
                        <button onClick={() => copyLink(inv)} data-testid={`admin-team-invite-copylink-btn-${inv.invite_id}`} className="btn-ghost" style={{ fontSize: 11 }}>Copy link</button>
                        <button onClick={() => cancelInvite(inv.invite_id)} data-testid={`admin-team-invite-cancel-btn-${inv.invite_id}`} className="btn-ghost" style={{ fontSize: 11, color: "#ef4444" }}>Cancel</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}

      {showInvite && (
        <CreateInviteDialog
          screens={screens}
          onClose={() => setShowInvite(false)}
          onCreated={(data) => { setShowInvite(false); setRevealToken({ token: data.token, email: data.email }); load(); }}
        />
      )}
      {revealToken && <TokenRevealDialog token={revealToken.token} email={revealToken.email} onClose={() => setRevealToken(null)} />}
      {overridesFor && (
        <OverridesDialog
          member={overridesFor}
          screens={screens}
          onClose={() => setOverridesFor(null)}
          onSaved={() => { setOverridesFor(null); load(); }}
        />
      )}
    </div>
  );
}

const sectionTitle = { fontSize: 12, color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: 0.5, margin: "0 0 8px" };
const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "'JetBrains Mono', monospace" };
const theadRowStyle = { color: "var(--text-faint)", textAlign: "left", borderBottom: "1px solid var(--border)" };
const rowStyle = { borderBottom: "1px solid rgba(255,255,255,0.04)", color: "var(--text-dim)" };
const th = { padding: "8px 10px", fontWeight: 500 };
const td = { padding: "7px 10px" };
const overlayStyle = { position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 };
const dialogStyle = { background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8, padding: 20, width: 380 };
const inputStyle = { width: "100%", padding: "8px 10px", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)", fontSize: 13 };
