/**
 * OraDirect.actionProposal.test.jsx — 2026-11-25 (ora-fix F7)
 *
 * The v2 engine emits `state` / `tool_call` / `tool_result` /
 * `action_proposal` SSE events, but OraDirect's read loop handled
 * only legacy v1 types — so a turn doing real work sat on a hung
 * "thinking…" and a proposed catalog action never rendered its
 * approval card. Locks in, against the real `pages/OraDirect.jsx`:
 *   1. A stream carrying state + tool_call + tool_result + delta +
 *      action_proposal + final still delivers the final reply AND
 *      renders the approval card (Approve/Cancel) for the proposal.
 *   2. Approve posts { proposal_id } to /ora-chat/action/approve and
 *      flips the card to "Approved — action executed."
 *   3. Cancel posts { proposal_id } to /ora-chat/action/reject and
 *      flips the card to "Cancelled — nothing was run."
 *
 * NOTE: this tree's seed has no frontend package.json / node_modules,
 * so this file could not be executed on the fix VM — it is written to
 * run in the real repo's `yarn test` (same harness as
 * OraDirect.reverseEngineerSlash.test.jsx).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  API_BASE: "http://test.invalid",
  getToken: vi.fn(),
  setToken: vi.fn(),
}));
vi.mock("../../components/OraChatHouseRulesPanel", () => ({ default: () => null }));
vi.mock("../../components/VideoGenPanel", () => ({ default: () => null }));
vi.mock("../../components/OraPreviewPanel", () => ({ default: () => null }));
vi.mock("streamdown", () => ({ Streamdown: ({ children }) => <div>{children}</div> }));
vi.mock("../../components/ReaAnalysisCard", () => ({
  default: ({ jobId, url }) => (
    <div data-testid="rea-analysis-card-stub">{jobId}:{url}</div>
  ),
}));

import { api, getToken, setToken } from "../../lib/api";
import OraDirect from "../OraDirect.jsx";

// One SSE payload carrying every v2 event type the old loop dropped,
// in the order the engine yields them.
function sseResponse(events) {
  const text = events
    .map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`)
    .join("");
  const bytes = new TextEncoder().encode(text);
  let sent = false;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (!sent) {
            sent = true;
            return { value: bytes, done: false };
          }
          return { value: undefined, done: true };
        },
      }),
    },
  };
}

const PROPOSAL = {
  type: "action_proposal",
  proposal_id: "prop-1",
  action_id: "trigger_digest",
  name: "Send a digest email now",
  risk: "reversible",
  description: "Send the leak or funnel digest email to the admin now.",
  params: { kind: "leak", to: "founder@aurem.dev" },
  requires_approval: true,
};

function turnEvents() {
  return [
    { type: "state", state_as_of: "2026-11-25T20:06:00+00:00" },
    { type: "tool_call", name: "get_repo_tree", args: {} },
    { type: "tool_result", name: "get_repo_tree", summary: "tree (12 files)" },
    { type: "delta", content: "Here is the plan." },
    PROPOSAL,
    { type: "final", content: "Here is the plan.", tokens_in: 10,
      tokens_out: 2, proposal_id: "prop-1", model: "fake",
      config_label: "fake", ungrounded: [] },
  ];
}

beforeEach(() => {
  localStorage.clear();
  api.get.mockReset();
  api.post.mockReset();
  getToken.mockImplementation(() => "already-authed-ora-token");
  setToken.mockImplementation(() => {});
  localStorage.setItem("ora_pin_token", "already-authed-ora-token");
  api.get.mockImplementation((path) => {
    if (path === "/ora-chat/sessions") return Promise.resolve({ data: { sessions: [] } });
    if (path === "/ora-chat/usage") return Promise.resolve({ data: { budget: {} } });
    return Promise.resolve({ data: {} });
  });
  api.post.mockImplementation((path) => {
    if (path === "/ora-chat/sessions") {
      return Promise.resolve({ data: { session: { session_id: "sess-1" } } });
    }
    return Promise.resolve({ data: {} });
  });
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    if (String(url).includes("/ora-chat/message")) return sseResponse(turnEvents());
    if (String(url).includes("/action/approve")) {
      return { ok: true, status: 200,
               json: async () => ({ ok: true, result: { ok: true } }) };
    }
    if (String(url).includes("/action/reject")) {
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function sendMessage(text) {
  render(<OraDirect />);
  await waitFor(() => expect(api.post).toHaveBeenCalledWith(
    "/ora-chat/sessions", { title: "Quick chat" }));
  const input = await screen.findByTestId("ora-input");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
  // The v2 events must not swallow the stream: final reply lands AND
  // the approval card renders on the same turn.
  const card = await screen.findByTestId("ora-action-proposal");
  await waitFor(() => expect(screen.getByTestId("ora-msg-assistant"))
    .toHaveTextContent("Here is the plan."));
  return card;
}

describe("OraDirect — v2 action proposal card (ora-fix F7)", () => {
  it("renders the approval card with Approve/Cancel for an action_proposal event", async () => {
    const card = await sendMessage("Send the digest now");
    expect(card).toHaveTextContent("Send a digest email now");
    expect(card).toHaveTextContent("reversible");
    expect(screen.getByTestId("ora-proposal-approve")).toBeTruthy();
    expect(screen.getByTestId("ora-proposal-cancel")).toBeTruthy();
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/ora-chat/message"), expect.anything());
  });

  it("Approve posts the proposal_id and flips the card to approved", async () => {
    await sendMessage("Send the digest now");
    fireEvent.click(screen.getByTestId("ora-proposal-approve"));
    await waitFor(() => expect(screen.getByTestId("ora-proposal-status"))
      .toHaveTextContent("Approved"));
    const call = global.fetch.mock.calls.find(([u]) =>
      String(u).includes("/action/approve"));
    expect(call).toBeTruthy();
    expect(JSON.parse(call[1].body)).toEqual({ proposal_id: "prop-1" });
    expect(call[1].method).toBe("POST");
  });

  it("Cancel posts the proposal_id and flips the card to cancelled", async () => {
    await sendMessage("Send the digest now");
    fireEvent.click(screen.getByTestId("ora-proposal-cancel"));
    await waitFor(() => expect(screen.getByTestId("ora-proposal-status"))
      .toHaveTextContent("Cancelled"));
    const call = global.fetch.mock.calls.find(([u]) =>
      String(u).includes("/action/reject"));
    expect(call).toBeTruthy();
    expect(JSON.parse(call[1].body)).toEqual({ proposal_id: "prop-1" });
  });
});
