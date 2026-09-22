/** Server-owned, per-turn evidence. Provider names are never review receipts. */
export function hasVerifiedReview(interaction) {
  const evidence = interaction?.review_evidence;
  return interaction?.version === 1 && evidence?.status === "passed" &&
    !!evidence?.review_id && !!interaction?.content_hash &&
    evidence.content_hash === interaction.content_hash &&
    evidence.request_id === interaction.request_id;
}

export function interactionFields(payload) {
  const interaction = payload?.interaction;
  return {
    ...(interaction ? { interaction } : {}),
    turnState: interaction?.turn_state ?? payload?.turn_state ?? null,
    continueAvailable: interaction?.continue_available ?? payload?.continue_available ?? false,
    failureMode: interaction?.failure_mode ?? payload?.failure_mode ?? null,
    recoveryAction: interaction?.recovery_action ?? payload?.recovery_action ?? null,
    turn_state: interaction?.turn_state ?? payload?.turn_state ?? null,
    continue_available: interaction?.continue_available ?? payload?.continue_available ?? false,
    failure_mode: interaction?.failure_mode ?? payload?.failure_mode ?? null,
  };
}