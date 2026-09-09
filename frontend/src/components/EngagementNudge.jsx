/**
 * components/EngagementNudge.jsx — Mandatory Repo-Connect Gate +
 * Post-Connect Engagement Nudge (2026-09-09), Part 2.
 *
 * Two variants, both rendered in-chat (a slim strip just above
 * ChatPanel — never a separate modal, per the ticket):
 *
 *   "post_skip" — user explicitly skipped the mandatory connect gate
 *     (Dashboard renders this only while projectCount === 0 && the
 *     user has skipped). Capped server-side at 3 lifetime shows, and
 *     capped here at once per browser session so it can never repeat
 *     the "Unlock memory" banner fatigue problem. Disappears the
 *     instant projectCount > 0 — that's just the parent unmounting
 *     it, no extra code needed here.
 *
 *   "post_scan" — user connected + ran a scan/opened the graph, but
 *     sent zero real task messages since. Content is REQUIRED, never
 *     a blank placeholder: the backend always returns a real task
 *     suggestion (either the actual top scan finding, or the named
 *     fallback line if that data is missing).
 */
import React, { useEffect, useRef, useState } from "react";
import { ArrowRight, Sparkles, X } from "lucide-react";
import { api } from "../lib/api";

function sessionCapKey(variant) {
  return `aurem_nudge_${variant}_shown_session`;
}

export function EngagementNudge({ variant, projectId, onConnect }) {
  const [visible, setVisible] = useState(false);
  const [prefillTask, setPrefillTask] = useState(null);
  const firedRef = useRef(false);

  useEffect(() => {
    if (firedRef.current) return;
    firedRef.current = true;

    let sessionKey;
    try {
      sessionKey = sessionCapKey(variant);
      if (sessionStorage.getItem(sessionKey)) return;
    } catch { /* private mode — proceed without the session cap */ }

    (async () => {
      if (variant === "post_scan") {
        const r = await api.get("/onboarding/nudge/post-scan-state", {
          params: { project_id: projectId },
        }).catch(() => null);
        if (!r?.data?.show) return;
        setPrefillTask(r.data.prefill_task);
      }
      const shownRes = await api.post("/onboarding/nudge/shown", {
        trigger: variant, project_id: projectId || null,
      }).catch(() => null);
      if (shownRes?.data?.capped) return;
      try { sessionStorage.setItem(sessionKey, "1"); } catch { /* ignore */ }
      setVisible(true);
    })();
  }, [variant, projectId]);

  if (!visible) return null;

  const isPostSkip = variant === "post_skip";

  return (
    <div
      data-testid={`engagement-nudge-${variant}`}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "9px 14px", margin: "0 0 4px",
        background: "rgba(245,158,11,0.08)",
        border: "0.5px solid rgba(245,158,11,0.22)",
        borderRadius: 8, fontSize: 12.5,
      }}
    >
      <Sparkles size={14} color="#f59e0b" style={{ flexShrink: 0 }} />
      <span style={{ flex: 1, color: "var(--text-secondary, #cbd5e1)" }}>
        {isPostSkip
          ? "Connect your repo so ORA can actually ship fixes for you — not just chat about them."
          : `Try: '${prefillTask}'`}
      </span>
      <button
        type="button"
        data-testid={`engagement-nudge-${variant}-action`}
        onClick={() => {
          setVisible(false);
          if (isPostSkip) {
            onConnect?.();
          } else {
            window.dispatchEvent(new CustomEvent("aurem:starter-pick", {
              detail: { prompt: prefillTask },
            }));
          }
        }}
        style={{
          display: "flex", alignItems: "center", gap: 4,
          background: "rgba(245,158,11,0.16)", border: "none",
          borderRadius: 6, padding: "4px 10px", fontSize: 12,
          fontWeight: 600, color: "#fbbf24", cursor: "pointer",
          whiteSpace: "nowrap",
        }}
      >
        {isPostSkip ? "Connect" : "Try it"} <ArrowRight size={12} />
      </button>
      <button
        type="button"
        data-testid={`engagement-nudge-${variant}-dismiss`}
        onClick={() => setVisible(false)}
        aria-label="Dismiss"
        style={{
          background: "transparent", border: "none", padding: 2,
          color: "var(--text-faint, #64748b)", cursor: "pointer",
          display: "flex", flexShrink: 0,
        }}
      >
        <X size={13} />
      </button>
    </div>
  );
}

export default EngagementNudge;
