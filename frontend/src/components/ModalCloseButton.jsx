import React from "react";
import { X } from "lucide-react";

// One shared ✕ so every popup has the same always-visible, keyboard-
// reachable way out. The parent card must be `position: relative`.
export default function ModalCloseButton({
  onClick, testId = "modal-close", label = "Close", disabled = false, style,
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={(e) => { e.stopPropagation(); onClick?.(e); }}
      style={{
        position: "absolute", top: 8, right: 8,
        width: 32, height: 32,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        background: "transparent", border: "none", borderRadius: 6,
        color: "var(--text-dim, #94a3b8)",
        cursor: disabled ? "not-allowed" : "pointer",
        ...style,
      }}
    >
      <X size={16} aria-hidden="true" />
    </button>
  );
}
