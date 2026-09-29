import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { streamChat } from "../api";
import { mapHistoryTurn } from "../../utils/dateDividers";

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

it("delivers the actual proposal SSE event and preserves its done fields", async () => {
  const event = { type: "scope_confirmation", proposal: { id: "p1", prompt: "saved" } };
  const done = { done: true, awaiting_scope_confirmation: true, scope_confirmation_proposal_id: "p1", scope_confirmation_prompt: "saved" };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(`data: ${JSON.stringify(event)}\n\ndata: ${JSON.stringify(done)}\n\n`, {
    headers: { "Content-Type": "text/event-stream" },
  })));
  const onScopeConfirmation = vi.fn(), onDone = vi.fn();
  await streamChat({ prompt: "saved", sessionId: "s1", onScopeConfirmation, onDone });
  expect(onScopeConfirmation).toHaveBeenCalledWith(event);
  expect(onDone).toHaveBeenCalledWith(done);
});

it("serializes the button proposal ID and exposes the real execution receipt", async () => {
  const receipt = { proposal_id: "p1", status: "succeeded", task_id: "t1", commit_sha: "a".repeat(40) };
  const fetcher = vi.fn().mockResolvedValue(new Response(`data: ${JSON.stringify({ done: true, scope_execution: receipt })}\n\n`));
  vi.stubGlobal("fetch", fetcher);
  const onDone = vi.fn();
  await streamChat({ prompt: "Confirm and proceed", projectId: "project1", sessionId: "s1", scopeConfirmedProposalId: "p1", onDone });
  const body = JSON.parse(fetcher.mock.calls[0][1].body);
  expect(body.scope_confirmed_proposal_id).toBe("p1");
  expect(body.session_id).toBe("s1");
  expect(onDone.mock.calls[0][0].scope_execution).toEqual(receipt);
});

it("restores a pending confirmation and saved prompt from database history", () => {
  const turn = mapHistoryTurn({ role: "assistant", content: "scope", awaiting_scope_confirmation: true,
    scope_confirmation_proposal_id: "p1", scope_confirmation_prompt: "saved" });
  expect(turn.awaitingScopeConfirmation).toBe(true);
  expect(turn.scopeConfirmationProposalId).toBe("p1");
  expect(turn.scopeConfirmationPrompt).toBe("saved");
});