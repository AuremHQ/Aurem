/**
 * components/admin/RequestAccessModal.jsx — AT3.
 *
 * Self-service "ask the founder" entry point for a team member who
 * doesn't have a screen they need. Calls POST /admin/team/access-
 * requests (self-service, never grants access by itself — the Owner
 * reviews + resolves it from the "Access requests" panel on
 * /admin/team; approving is just their own signal to go set it up,
 * see admin_team.py's resolve_access_request).
 */
import React, { useState } from "react";
import { api } from "../../lib/api";
import { toast } from "../Toast";
import { ADMIN_NAV } from "../../lib/adminNav";

const overlayStyle = { position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 };
const dialogStyle = { background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8, padding: 20, width: 380 };
const inputStyle = { width: "100%", padding: "8px 10px", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)", fontSize: 13 };

export default function RequestAccessModal({ accessibleIds, onClose }) {
  // Only offer screens the member doesn't already have — asking
  // again for something they can already open would be confusing.
  const options = ADMIN_NAV.filter((n) => !accessibleIds.has(n.id));
  const [screenId, setScreenId] = useState(options[0]?.id || "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e) {
    e.preventDefault();
    const item = ADMIN_NAV.find((n) => n.id === screenId);
    const screenKey = item?.screenKeys?.[0] || item?.id;
    if (!screenKey) { setErr("Pick a screen first"); return; }
    setBusy(true); setErr("");
    try {
      await api.post("/admin/team/access-requests", { screen_key: screenKey, note: note.trim() });
      toast("Request sent to the founder");
      onClose();
    } catch (e2) {
      setErr(e2?.response?.data?.detail || e2.message || "Failed to send request");
    } finally { setBusy(false); }
  }

  return (
    <div data-testid="admin-request-access-dialog" style={overlayStyle}>
      <form onSubmit={submit} style={dialogStyle}>
        <h3 style={{ margin: "0 0 6px", fontSize: 14, color: "var(--text)" }}>Request admin access</h3>
        <p style={{ fontSize: 11, color: "var(--text-faint)", margin: "0 0 12px" }}>
          The founder reviews this — approving doesn't hand you the screen automatically, it's their cue to set it up for you.
        </p>
        <label style={{ fontSize: 11, color: "var(--text-dim)", display: "block", marginBottom: 4 }}>Screen</label>
        <select
          data-testid="admin-request-access-screen-select"
          value={screenId}
          onChange={(e) => setScreenId(e.target.value)}
          style={{ ...inputStyle, marginBottom: 10 }}
        >
          {options.length === 0 && <option value="">You already have everything</option>}
          {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
        <label style={{ fontSize: 11, color: "var(--text-dim)", display: "block", marginBottom: 4 }}>Why (optional)</label>
        <textarea
          data-testid="admin-request-access-note-input"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          style={{ ...inputStyle, resize: "vertical" }}
          placeholder="e.g. need to rotate the Stripe key"
        />
        {err && <div data-testid="admin-request-access-error" style={{ color: "#ef4444", fontSize: 11, marginTop: 8 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14 }}>
          <button type="button" onClick={onClose} className="btn-ghost" data-testid="admin-request-access-cancel-btn" style={{ fontSize: 12 }}>Cancel</button>
          <button type="submit" disabled={busy || options.length === 0} data-testid="admin-request-access-submit-btn" className="btn-primary" style={{ fontSize: 12 }}>
            {busy ? "Sending…" : "Send request"}
          </button>
        </div>
      </form>
    </div>
  );
}
