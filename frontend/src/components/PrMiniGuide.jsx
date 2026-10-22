/**
 * PrMiniGuide.jsx — R6 (overnight round), folded into the ship-UI
 * unification work per founder's ask.
 *
 * A plain-English, 3-step "what happens next" guide shown the FIRST
 * time a user is about to approve a ship. Dismissible; never shown
 * again once dismissed (per-browser, localStorage — same pattern as
 * the existing "shown once" nudges elsewhere in this app, e.g.
 * LoopLiveFeed's `aurem_ps_shown_v1`).
 *
 * Self-contained: no new props needed from the 3 ship surfaces that
 * render it (ShipDialog, ShipConfirmModal) — it manages its own
 * "have I been dismissed" state, so wiring it in is a one-line add
 * with zero risk to the surrounding approve/ship logic.
 *
 * 2026-10 · Phase 1 fix (founder-reported 2026-10-21 incident) — the
 * `ship_via_pr` flag this component read (via `isShipViaPrEnabled()`)
 * only ever gated loop_engine.py's ship path. ShipDialog.jsx's
 * "Approve the fix" surface (Mode D chat -> shipViaCTO() ->
 * worker_git.py) is a COMPLETELY DIFFERENT backend that never checked
 * that flag — so whenever a user had it ON, this card promised "We'll
 * open a pull request" while worker_git.py silently pushed straight
 * to the base branch (commit b58fa79, TJSNDHU/Aurem). worker_git.py
 * now ALWAYS creates a branch + best-effort PR there regardless of
 * the flag (see worker_git.py's own 2026-10 comment), so this surface
 * must describe ITS OWN fixed, flag-independent behavior instead of
 * reading a flag it has nothing to do with. Callers on that surface
 * pass `mode="branch_and_pr"`; every other caller is unchanged.
 */
import React, { useState } from "react";
import { X, GitPullRequest, GitCommitHorizontal } from "lucide-react";
import { isShipViaPrEnabled } from "../lib/pageFlags";

const SEEN_KEY = "aurem_pr_mini_guide_seen_v1";

export default function PrMiniGuide({ mode = "auto" }) {
  const [dismissed, setDismissed] = useState(() => {
    try { return !!localStorage.getItem(SEEN_KEY); } catch { return false; }
  });

  if (dismissed) return null;

  function dismiss() {
    try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* quota — ignore */ }
    setDismissed(true);
  }

  if (mode === "branch_and_pr") {
    return (
      <div
        data-testid="pr-mini-guide"
        style={{
          display: "flex", alignItems: "flex-start", gap: 10,
          padding: "10px 12px", marginBottom: 8,
          background: "rgba(255,102,8,0.06)",
          border: "1px solid rgba(255,102,8,0.25)",
          borderRadius: 8, fontSize: 12, color: "var(--text, #F5F5F5)",
          maxWidth: 420,
        }}
      >
        <GitPullRequest size={16} style={{ flexShrink: 0, marginTop: 1, color: "#FF6608" }} />
        <div style={{ flex: 1, lineHeight: 1.55 }}>
          <strong>What happens next:</strong>
          <ol data-testid="pr-mini-guide-branch-pr-copy" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            <li>ORA commits to a new branch — never straight to main.</li>
            <li>It tries to open a draft PR for you to review. If GitHub
              won't let it open one, you'll still get the branch, ready
              to review yourself.</li>
            <li>Nothing lands on your main branch until you merge.</li>
          </ol>
        </div>
        <button
          type="button"
          data-testid="pr-mini-guide-dismiss"
          onClick={dismiss}
          aria-label="Dismiss"
          style={{
            background: "none", border: "none", padding: 2,
            color: "var(--text-faint, #8A8A8A)", cursor: "pointer", flexShrink: 0,
          }}
        >
          <X size={13} />
        </button>
      </div>
    );
  }

  // 2026-10 — C4 fix. This used to hard-code the pull-request story
  // regardless of whether ship_via_pr was actually on — false in
  // production, where every ship commits straight to the base
  // branch. Reads the SAME flag the real ship path checks (see
  // lib/pageFlags.js), so the copy can't drift from reality again.
  const prMode = isShipViaPrEnabled();

  return (
    <div
      data-testid="pr-mini-guide"
      style={{
        display: "flex", alignItems: "flex-start", gap: 10,
        padding: "10px 12px", marginBottom: 8,
        background: "rgba(255,102,8,0.06)",
        border: "1px solid rgba(255,102,8,0.25)",
        borderRadius: 8, fontSize: 12, color: "var(--text, #F5F5F5)",
        maxWidth: 420,
      }}
    >
      {prMode
        ? <GitPullRequest size={16} style={{ flexShrink: 0, marginTop: 1, color: "#FF6608" }} />
        : <GitCommitHorizontal size={16} style={{ flexShrink: 0, marginTop: 1, color: "#FF6608" }} />}
      <div style={{ flex: 1, lineHeight: 1.55 }}>
        <strong>What happens next:</strong>
        {prMode ? (
          <ol data-testid="pr-mini-guide-pr-copy" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            <li>We'll open a pull request on your GitHub.</li>
            <li>Review it there if you like, or just click Merge.</li>
            <li>Done — it's live on your site.</li>
          </ol>
        ) : (
          <ol data-testid="pr-mini-guide-direct-copy" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            <li>ORA commits directly to your main branch.</li>
            <li>No PR to review first — it's live right away.</li>
            <li>Changed your mind? You can roll back anytime.</li>
          </ol>
        )}
      </div>
      <button
        type="button"
        data-testid="pr-mini-guide-dismiss"
        onClick={dismiss}
        aria-label="Dismiss"
        style={{
          background: "none", border: "none", padding: 2,
          color: "var(--text-faint, #8A8A8A)", cursor: "pointer", flexShrink: 0,
        }}
      >
        <X size={13} />
      </button>
    </div>
  );
}
