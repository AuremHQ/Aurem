/**
 * components/EngagementNudge.jsx — Mandatory Repo-Connect Gate +
 * Post-Connect Engagement Nudge (2026-09-09), Part 2.
 *
 * 2026-09-09 — founder ticket Part 0: the FIRST version of this
 * component was a full-width, always-visible in-chat strip — which
 * the founder correctly called out as just a THIRD competing banner
 * (after the removed ConnectRepoBanner + the mandatory gate), the
 * exact clutter problem this whole ticket exists to fix. Rewritten
 * as a small "toggle-badge", modeled directly on the already-shipped
 * ORA GUIDE mascot (OraGuideMascot.jsx) pattern: a small floating
 * pill that starts COLLAPSED (near-zero visual footprint — icon +
 * a couple words), and only reveals its full message + action when
 * the user deliberately clicks it. Floats bottom-LEFT (ORA GUIDE
 * owns bottom-right) so the two can never collide.
 *
 * Two variants, backend-driven state unchanged from the original
 * build (only the visual presentation changed here):
 *
 *   "post_skip" — user explicitly skipped the mandatory connect gate.
 *     Capped server-side at 3 lifetime shows, client-capped at once
 *     per browser session, vanishes forever the instant they connect
 *     (parent Dashboard just stops rendering it).
 *
 *   "post_scan" — user connected + ran a scan/opened the graph, but
 *     sent zero real task messages since. Content is REQUIRED, never
 *     blank: the real top scan finding, or the named fallback task.
 */
import React, { useEffect, useRef, useState } from "react";
import { ArrowRight, Sparkles } from "lucide-react";
import { api } from "../lib/api";
import { Z_ENGAGEMENT_NUDGE } from "../lib/zIndex";

function sessionCapKey(variant) {
  return `aurem_nudge_${variant}_shown_session`;
}

export function EngagementNudge({ variant, projectId, onConnect }) {
  const [eligible, setEligible] = useState(false);
  const [open, setOpen] = useState(false);
  const [prefillTask, setPrefillTask] = useState(null);
  const firedRef = useRef(false);

  // "Shown" (eligible to render, even collapsed) is decided once per
  // mount — same funnel semantics as the original build, only the
  // ON-SCREEN result changed from "always-expanded strip" to
  // "collapsed badge, opens on click".
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
      setEligible(true);
    })();
  }, [variant, projectId]);

  if (!eligible) return null;

  const isPostSkip = variant === "post_skip";
  const badgeLabel = isPostSkip ? "Connect repo" : "Try a task";

  function act() {
    setOpen(false);
    setEligible(false);
    if (isPostSkip) {
      onConnect?.();
    } else {
      window.dispatchEvent(new CustomEvent("aurem:starter-pick", {
        detail: { prompt: prefillTask },
      }));
    }
  }

  return (
    <div
      data-testid={`engagement-nudge-${variant}`}
      style={{ position: "fixed", bottom: 20, left: 20, zIndex: Z_ENGAGEMENT_NUDGE }}
    >
      {open && (
        <div
          data-testid={`engagement-nudge-${variant}-bubble`}
          style={{
            position: "absolute", bottom: 44, left: 0, width: 260,
            background: "#141414", border: "1px solid rgba(245,158,11,0.28)",
            borderRadius: 12, padding: "12px 14px",
            boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
          }}
        >
          <div style={{ fontSize: 12.5, color: "#f8fafc", lineHeight: 1.5, marginBottom: 10 }}>
            {isPostSkip
              ? "Connect your repo so ORA can actually ship fixes for you — not just chat about them."
              : `Try: '${prefillTask}'`}
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <button
              type="button"
              data-testid={`engagement-nudge-${variant}-action`}
              onClick={act}
              style={{
                display: "flex", alignItems: "center", gap: 4,
                background: "rgba(245,158,11,0.16)", border: "none",
                borderRadius: 6, padding: "5px 10px", fontSize: 12,
                fontWeight: 600, color: "#fbbf24", cursor: "pointer",
              }}
            >
              {isPostSkip ? "Connect" : "Try it"} <ArrowRight size={12} />
            </button>
            <button
              type="button"
              data-testid={`engagement-nudge-${variant}-dismiss`}
              onClick={() => { setOpen(false); setEligible(false); }}
              style={{ background: "transparent", border: "none", padding: 4,
                       color: "var(--text-faint, #64748b)", cursor: "pointer", fontSize: 11 }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <button
        type="button"
        data-testid={`engagement-nudge-${variant}-badge`}
        aria-label={badgeLabel}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 6,
          background: "#141414", border: "1px solid rgba(245,158,11,0.32)",
          borderRadius: 999, padding: "7px 12px",
          fontSize: 11.5, fontWeight: 600, color: "#fbbf24",
          cursor: "pointer",
          boxShadow: open ? "none" : "0 0 0 3px rgba(245,158,11,0.14), 0 4px 16px rgba(0,0,0,0.4)",
        }}
      >
        <Sparkles size={12} />
        {badgeLabel}
      </button>
    </div>
  );
}

export default EngagementNudge;
