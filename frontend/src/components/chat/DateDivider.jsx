/**
 * components/chat/DateDivider.jsx — WhatsApp/Telegram-style chat
 * history date divider: a centered, muted, non-interactive separator
 * line with the date text centered on it.
 *
 * Pure presentational — given a computed `text` ("Today" / "Yesterday"
 * / "15 September 2026"), renders the divider row. Scrolls with the
 * message list (a normal flow list item, not pinned/sticky/floating).
 * Theme-aware via CSS variables already used across the app's chat UI
 * (--text-faint / --border, defined for both light and dark themes).
 */
import React from "react";

export function DateDivider({ text }) {
  if (!text) return null;
  return (
    <div
      data-testid="chat-date-divider"
      role="separator"
      aria-hidden="true"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        margin: "18px 0",
        userSelect: "none",
        pointerEvents: "none",
      }}
    >
      <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
      <span
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: "var(--text-faint)",
          whiteSpace: "nowrap",
          letterSpacing: "0.02em",
        }}
      >
        {text}
      </span>
      <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
    </div>
  );
}

export default DateDivider;
