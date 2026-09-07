/**
 * components/FirstScanNudge.jsx — P2-B (First-Experience Wave).
 *
 * New-user nudge: shown once a repo is connected but NO scan has ever run
 * for it (GET /onboarding/first-scan/status → "skipped") and the user
 * hasn't typed anything yet. One click prefills the chat with "/scan".
 * Dismissed per project in localStorage; never shown again after that.
 */
import React, { useEffect, useState } from "react";
import { Search, X } from "lucide-react";
import { api } from "../lib/api";

const KEY = (pid) => `aurem_first_scan_nudge_dismissed_${pid}`;

export default function FirstScanNudge({ projectId, hasUserMessage }) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (!projectId || projectId === "home") { setShow(false); return undefined; }
    let cancelled = false;
    try { if (localStorage.getItem(KEY(projectId))) return undefined; } catch { /* ignore */ }
    api.get("/onboarding/first-scan/status", { params: { project_id: projectId } })
      .then((r) => { if (!cancelled && r?.data?.status === "skipped") setShow(true); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [projectId]);

  if (!show || hasUserMessage) return null;

  const dismiss = () => {
    try { localStorage.setItem(KEY(projectId), "1"); } catch { /* ignore */ }
    setShow(false);
  };
  const scan = () => {
    try { window.dispatchEvent(new CustomEvent("ora:prefill", { detail: { message: "/scan" } })); } catch { /* ignore */ }
    dismiss();
  };

  return (
    <div
      data-testid="first-scan-nudge"
      role="status"
      style={{
        display: "flex", alignItems: "center", gap: 10,
        margin: "0 clamp(16px, 17.25%, 240px) 8px",
        padding: "8px 12px", borderRadius: 8,
        background: "rgba(232,160,32,0.08)", border: "1px solid rgba(232,160,32,0.35)",
        color: "#e6ebf3", fontSize: 13,
      }}
    >
      <Search size={14} style={{ color: "#E8A020", flexShrink: 0 }} />
      <span style={{ flex: 1 }}>
        New here? Start with a quick scan of your site — I'll tell you what to fix first.
      </span>
      <button
        type="button"
        data-testid="first-scan-nudge-scan-btn"
        onClick={scan}
        style={{
          background: "#E8A020", color: "#0d1117", border: "none", borderRadius: 6,
          padding: "5px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer",
        }}
      >
        Scan my site
      </button>
      <button
        type="button"
        aria-label="Dismiss"
        data-testid="first-scan-nudge-dismiss"
        onClick={dismiss}
        style={{ background: "transparent", border: "none", color: "#8b949e", cursor: "pointer", padding: 2 }}
      >
        <X size={14} />
      </button>
    </div>
  );
}
