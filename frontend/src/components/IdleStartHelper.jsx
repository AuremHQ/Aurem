/**
 * IdleStartHelper.jsx — 2026-02 "STUCK USERS FIX" Part 1.2.
 *
 * Founder spec: "If the user is still idle 3 minutes after
 * graph_built (tab open), show a small in-app helper, once."
 *
 * Deliberately separate from the pre-existing `EngagementNudge`
 * ("post_scan" variant) — that component already covers a related
 * but different moment (mascot bubble, prefill-only, its own exact
 * required copy from an earlier round) and isn't touched here to
 * avoid regressing it. This is the founder's NEW literal spec: a
 * small on-screen banner (not the mascot), exact new copy, and a
 * real one-click auto-send action (not a prefill).
 *
 * Reuses the EXISTING `/onboarding/nudge/post-scan-state` endpoint
 * as the eligibility check (it already answers exactly the question
 * "connected + graph/scan done, but sent zero real task messages
 * since?") instead of duplicating that backend logic.
 */
import React, { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { api } from "../lib/api";

const IDLE_MS = 3 * 60_000;
const SESSION_KEY = "aurem_idle_start_helper_shown";

export default function IdleStartHelper({ projectId, visible }) {
  const [show, setShow] = useState(false);
  const timerRef = useRef(null);
  const firedRef = useRef(false);

  useEffect(() => {
    firedRef.current = false;
    setShow(false);
    if (!visible || !projectId) return undefined;
    try {
      if (sessionStorage.getItem(SESSION_KEY)) return undefined;
    } catch {
      /* private mode — proceed without the session cap */
    }

    timerRef.current = setTimeout(async () => {
      if (firedRef.current) return;
      try {
        const r = await api.get("/onboarding/nudge/post-scan-state", {
          params: { project_id: projectId },
        });
        if (!r?.data?.show) return;
        firedRef.current = true;
        try { sessionStorage.setItem(SESSION_KEY, "1"); } catch { /* ignore */ }
        setShow(true);
      } catch {
        /* best-effort — never surface an error for a helpful nudge */
      }
    }, IDLE_MS);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
    };
  }, [projectId, visible]);

  if (!show) return null;

  function startScan() {
    setShow(false);
    window.dispatchEvent(new CustomEvent("aurem:starter-autosend", {
      detail: { text: "Scan my project for problems and show me the top 3 things to fix." },
    }));
  }

  return (
    <div
      data-testid="idle-start-helper"
      style={{
        margin: "0 auto 16px", maxWidth: 520,
        display: "flex", alignItems: "center", gap: 12,
        background: "var(--bg-elev)",
        border: "1px solid var(--border-strong)",
        borderRadius: 10, padding: "10px 14px",
      }}
    >
      <div style={{ flex: 1, fontSize: 12, color: "var(--text)" }}>
        Want me to start? I'll scan your project and show the top 3 things
        to fix — takes about a minute.
      </div>
      <button
        data-testid="idle-start-helper-yes"
        onClick={startScan}
        style={{
          padding: "6px 12px", borderRadius: 7, border: "none",
          background: "#f59e0b", color: "#0a0c10",
          fontSize: 11.5, fontWeight: 600, cursor: "pointer",
          whiteSpace: "nowrap",
        }}
      >
        Yes, start
      </button>
      <button
        data-testid="idle-start-helper-not-now"
        onClick={() => setShow(false)}
        title="Not now"
        style={{
          background: "transparent", border: "none",
          color: "var(--text-faint)", cursor: "pointer", padding: 4,
          display: "flex",
        }}
      >
        <X size={13} />
      </button>
    </div>
  );
}
