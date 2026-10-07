/**
 * GraphReadyCard.jsx — 2026-02 "STUCK USERS FIX" Part 1.1.
 *
 * Real production alerts showed users connecting GitHub, the
 * codebase graph finishing, and then... nothing. No first task
 * started for 60+ min (jasonoskins6, davis.scott29, kesseguillaume)
 * because they landed in an empty chat with zero next step.
 *
 * Polls `GET /cto/projects/{id}/graph` (same endpoint GraphPanel.jsx
 * already polls) until the build finishes, then shows a plain-
 * English "you're ready" card with 3 one-click buttons. Unlike the
 * existing `ora-inject` / `aurem:starter-pick` events (which only
 * prefill the composer — "never auto-sends", by design, for THOSE
 * flows), these buttons dispatch `aurem:starter-autosend` so the
 * very first click actually starts a real task with zero typing —
 * exactly the founder's spec ("The first click starts a real task
 * immediately").
 *
 * Only renders for a genuinely fresh project (zero messages so far);
 * disappears the moment a button is clicked or the graph errors out
 * with no further action needed from this component (GraphPanel
 * itself still offers the full retry UI for that case).
 */
import React, { useEffect, useRef, useState } from "react";
import { Sparkles, Search, Wrench, BookOpen } from "lucide-react";
import { api } from "../lib/api";

const POLL_MS = 4000;
const MAX_POLL_MS = 5 * 60_000; // matches GraphPanel's own ceiling

const STARTERS = [
  {
    testId: "graph-ready-scan-btn",
    icon: Search,
    label: "Scan my project for problems",
    prompt: "Scan my project for problems and list what you find, worst first.",
  },
  {
    testId: "graph-ready-fix-btn",
    icon: Wrench,
    label: "Fix my most important issue",
    prompt: "Find the most important issue in my project and fix it.",
  },
  {
    testId: "graph-ready-explain-btn",
    icon: BookOpen,
    label: "Explain my project to me",
    prompt: "Explain my project to me — what does it do and how is it structured?",
  },
];

export default function GraphReadyCard({ projectId, visible }) {
  const [fileCount, setFileCount] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const pollRef = useRef(null);
  const startedRef = useRef(0);

  useEffect(() => {
    setDismissed(false);
    setFileCount(null);
    if (!visible || !projectId) return undefined;
    startedRef.current = Date.now();

    async function poll() {
      try {
        const r = await api.get(`/cto/projects/${projectId}/graph`);
        const status = r.data?.status;
        if (status === "ready") {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setFileCount(r.data?.graph?.file_count ?? 0);
        } else if (status === "error" || Date.now() - startedRef.current > MAX_POLL_MS) {
          // Genuinely broken/too slow — GraphPanel's manual retry
          // path is the recovery mechanism; don't show a stale card.
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        // keep polling — a single transient failure shouldn't hide
        // the card for the rest of the session.
      }
    }
    poll();
    pollRef.current = setInterval(poll, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [projectId, visible]);

  if (!visible || dismissed || fileCount === null) return null;

  function pick(prompt) {
    setDismissed(true);
    window.dispatchEvent(new CustomEvent("aurem:starter-autosend", {
      detail: { text: prompt },
    }));
  }

  return (
    <div
      data-testid="graph-ready-card"
      style={{
        margin: "0 auto 16px", maxWidth: 520,
        background: "var(--panel)",
        border: "1px solid rgba(34,197,94,0.28)",
        borderRadius: 12, padding: "16px 18px",
        boxShadow: "0 8px 24px -8px rgba(34,197,94,0.12)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <Sparkles size={15} color="#22c55e" />
        <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--text)" }}>
          Your project is connected. ORA read {fileCount} file{fileCount === 1 ? "" : "s"}.
        </span>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--text-faint)", marginBottom: 12 }}>
        Pick one — ORA starts right away, no typing needed.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {STARTERS.map(({ testId, icon: Icon, label, prompt }) => (
          <button
            key={testId}
            data-testid={testId}
            onClick={() => pick(prompt)}
            style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "10px 14px", borderRadius: 8, textAlign: "left",
              border: "1px solid var(--border)",
              background: "var(--bg-elev)", color: "var(--text)",
              fontSize: 12.5, fontWeight: 500, cursor: "pointer",
              transition: "border-color 0.15s, background 0.15s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "#22c55e";
              e.currentTarget.style.background = "rgba(34,197,94,0.06)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "var(--border)";
              e.currentTarget.style.background = "var(--bg-elev)";
            }}
          >
            <Icon size={14} color="#22c55e" style={{ flexShrink: 0 }} />
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
