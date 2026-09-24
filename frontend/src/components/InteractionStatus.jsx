import React from "react";

const LABELS = {
  success: "Response delivered",
  partial: "Incomplete",
  blocked: "Blocked — clarification needed",
  error: "Failed — no completion confirmed",
};

export const InteractionStatus = ({ interaction, index }) => {
  if (interaction?.version !== 1) return null;
  return (
    <div data-testid={`interaction-status-${index}`} role="status"
      style={{ fontSize: 12, marginBottom: 8, color: interaction.turn_state === "success" ? "var(--text-muted)" : "var(--warning, #ad6500)", overflowWrap: "anywhere" }}>
      {LABELS[interaction.turn_state] || "Completion not verified"}
    </div>
  );
};