import React from "react";

/**
 * VerificationLevelBadge — Trust & Verification round, Enhancement 2
 * (User-Visible Verification Level).
 *
 * Renders the EXACT backend-computed `verification_level_display`
 * string (services/verification.py's `display_level()` — the client
 * never computes this itself, it only renders what the server sent).
 * `verificationStatus` (`pass` | `fail` | `inconclusive` | undefined)
 * drives the tint; a missing/legacy task with neither field renders
 * the neutral "Not verified" state — never fabricated as passing.
 */
const _TONE = {
  pass:         { bg: "rgba(90,200,120,0.12)",  fg: "var(--ok, #5ac878)",   border: "rgba(90,200,120,0.35)" },
  fail:         { bg: "rgba(230,90,90,0.12)",   fg: "var(--danger, #e65a5a)", border: "rgba(230,90,90,0.35)" },
  needs_review: { bg: "rgba(255,197,96,0.14)",  fg: "var(--accent-2, #e8a020)", border: "rgba(255,197,96,0.4)" },
  inconclusive: { bg: "rgba(160,160,160,0.12)", fg: "var(--text-faint)",    border: "var(--border)" },
};

export default function VerificationLevelBadge({ verificationStatus, verificationLevelDisplay, needsReview }) {
  const status = needsReview ? "needs_review" : (verificationStatus || "inconclusive");
  const tone = _TONE[status] || _TONE.inconclusive;
  const label = needsReview
    ? "Needs review"
    : (verificationLevelDisplay || "Not verified");

  return (
    <span
      data-testid="verification-level-badge"
      data-verification-status={status}
      title="What was actually checked for this task — see spec Enhancement 2"
      style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        fontSize: 11, fontWeight: 600, letterSpacing: 0.2,
        padding: "2px 8px", borderRadius: 999,
        background: tone.bg, color: tone.fg, border: `1px solid ${tone.border}`,
      }}
    >
      {label}
    </span>
  );
}
