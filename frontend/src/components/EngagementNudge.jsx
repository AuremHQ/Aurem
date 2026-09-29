/**
 * components/EngagementNudge.jsx — Mandatory Repo-Connect Gate +
 * Post-Connect Engagement Nudge (2026-09-09), Part 2.
 *
 * 2026-09-09 — founder ticket Part 0 (round 2): the founder circled
 * TWO still-competing entry points in a screenshot — this component's
 * own floating "Try a task" / "Connect repo" badge (bottom-left) AND
 * the separate ORA GUIDE mascot (bottom-right) — and said "don't show
 * anything twice... only one, through the robot." Rewritten headless:
 * this component renders NOTHING itself anymore. It keeps 100% of its
 * original eligibility logic (session cap, backend `/onboarding/
 * nudge/*` calls, the exact required copy incl. the named fallback
 * task) but hands the result to OraGuideMascot — the single floating
 * entry point — via a `window` custom event, the same cross-component
 * pattern already used by `aurem:starter-pick` / `aurem:ora-open`
 * elsewhere in this codebase. OraGuideMascot decides whether/when to
 * surface it (never simultaneously with its own stage bubble).
 *
 * Two variants, eligibility unchanged from the original build:
 *
 *   "post_skip" — user explicitly skipped the mandatory connect gate.
 *     Capped server-side at 3 lifetime shows, client-capped at once
 *     per browser session, vanishes forever the instant they connect
 *     (parent Dashboard just stops mounting this — projectCount > 0).
 *
 *   "post_scan" — user connected + ran a scan/opened the graph, but
 *     sent zero real task messages since. Content is REQUIRED, never
 *     blank: the real top scan finding, or the named fallback task.
 */
import { useEffect, useRef } from "react";
import { api } from "../lib/api";

function sessionCapKey(variant) {
  return `aurem_nudge_${variant}_shown_session`;
}

export function EngagementNudge({ variant, projectId }) {
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
      let message = variant === "post_skip"
        ? "Skipped connecting a repo? No problem — scan your website, try a demo fix, or connect anytime."
        : null;
      let prefillTask = null;
      if (variant === "post_scan") {
        const r = await api.get("/onboarding/nudge/post-scan-state", {
          params: { project_id: projectId },
        }).catch(() => null);
        if (!r?.data?.show) return;
        prefillTask = r.data.prefill_task;
        message = `Try: '${prefillTask}'`;
      }
      const shownRes = await api.post("/onboarding/nudge/shown", {
        trigger: variant, project_id: projectId || null,
      }).catch(() => null);
      if (shownRes?.data?.capped) return;
      try { sessionStorage.setItem(sessionKey, "1"); } catch { /* ignore */ }
      try {
        window.dispatchEvent(new CustomEvent("aurem:external-nudge", {
          detail: {
            variant, message, prefillTask,
            actionLabel: variant === "post_skip" ? "See options" : "Try it",
          },
        }));
      } catch { /* noop */ }
    })();
  }, [variant, projectId]);

  return null;
}

export default EngagementNudge;
