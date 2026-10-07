# Chat outcome contract

Every ORA chat turn ends in one of four outcomes. The server decides it from
evidence it holds, never from how the reply sounds. Code: `backend/services/turn_outcome.py`
(`decide`), attached to the turn envelope by `interaction_contract.finalize`
(only when `INTERACTION_CONTRACT_V1=1`, default off).

| Outcome | Meaning | Evidence required |
|---|---|---|
| `DONE` | Something real happened. | A tool call with `ok: true` that was not a forced pre-fetch, or a pending change executed (`commit-boundary-executor`). Listed in `evidence`. |
| `NEEDS_YOU` | Waiting on the user. | A proposed change (`aurem-handoff` block) awaiting approval, a "which change?" disambiguation, or a blocked/clarify state. |
| `NOTHING_DONE` | No tool result, no change. | `reason` says why: `answered` (plain answer), `announced_not_done` (reply promises work with nothing behind it, or the incomplete-turn notice fired), `nothing_pending` ("ok" with nothing stored), `cancelled`. |
| `FAILED` | The turn errored or came back empty. | `turn_state == "error"` or `commit-boundary-error`. |

Rules
- A reply that only *says* it did or will do work is `NOTHING_DONE`. Wording
  never upgrades an outcome; only a tool result or an executed change does.
- `turn_state` (success/partial/blocked/error) is unchanged for existing
  consumers. `outcome`, `reason`, `evidence` are additive, and
  `completion_basis` now carries the reason ("response_delivered" is no
  longer emitted).
- The UI label (`frontend/src/components/InteractionStatus.jsx`) follows
  `outcome`/`reason`. Envelopes without `outcome` keep the old labels.

Adding a new action safely
1. Make the action return a real result the server can see: a tool entry in
   `tool_invocations` with `tool` and `ok`, or a deterministic provider id
   mapped in `_PROVIDER_OUTCOMES`.
2. Never mark it `DONE` from text. If the action only proposes something,
   store it as a pending change so it maps to `NEEDS_YOU`.
3. Add a case to `backend/tests/test_regression_iter410_turn_outcome_from_evidence.py`
   (one for the success evidence, one for the "claims it but has no result" case).
4. Check both endpoints (`chat_send`, `/chat/stream`) with the replay test
   `backend/tests/test_chat_reliability_replay_phase0.py`.
