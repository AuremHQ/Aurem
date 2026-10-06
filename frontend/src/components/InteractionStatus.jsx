import React from "react";

const LABELS = {
  success: "Response delivered",
  partial: "Incomplete",
  blocked: "Blocked — clarification needed",
  error: "Failed — no completion confirmed",
};

// The server decides the outcome from evidence (backend/services/turn_outcome.py);
// when it is present the label follows it, never the bare transport state.
const NOTHING_DONE_LABELS = {
  answered: "Answered — no action taken",
  announced_not_done: "Not done yet — nothing was changed",
  nothing_pending: "Nothing to do — no change was waiting",
  cancelled: "Cancelled — nothing was changed",
};
const OUTCOME_LABELS = {
  DONE: "Done",
  NEEDS_YOU: "Waiting for you",
  FAILED: "Failed — no completion confirmed",
};

export const outcomeLabel = (interaction) => {
  const outcome = interaction?.outcome;
  if (!outcome) return LABELS[interaction?.turn_state] || "Completion not verified";
  if (outcome === "NOTHING_DONE") {
    return NOTHING_DONE_LABELS[interaction.reason] || "Nothing was changed";
  }
  return OUTCOME_LABELS[outcome] || "Completion not verified";
};

export const InteractionStatus = ({ interaction, index }) => {
  if (interaction?.version !== 1) return null;
  const quiet = interaction.outcome
    ? interaction.outcome === "DONE" || interaction.reason === "answered"
    : interaction.turn_state === "success";
  const evidence = (interaction.evidence || []).join(", ");
  return (
    <div data-testid={`interaction-status-${index}`} role="status" title={evidence || undefined}
      style={{ fontSize: 12, marginBottom: 8, color: quiet ? "var(--text-muted)" : "var(--warning, #ad6500)", overflowWrap: "anywhere" }}>
      {outcomeLabel(interaction)}
    </div>
  );
};
