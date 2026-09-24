import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { extractSuggestions } from "../chatTextUtils";
import { mapHistoryTurn } from "../dateDividers";
import { interactionFields, hasVerifiedReview } from "../interactionState";
import MessageBubble from "../../components/MessageBubble.jsx";

vi.mock("../../lib/api", () => ({
  api: {
    get: vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
  },
  getToken: () => null,
}));

vi.mock("../../components/Toast", () => ({ toast: vi.fn() }));

describe("interaction reliability contract (baseline, expected to fail pre-fix)", () => {
  it("extractSuggestions should honor interaction contract and avoid arbitrary quote chips", () => {
    const content = 'In this technical note, try again and say "do not parse this" as an example.';
    const interaction = { version: 1, turn_state: "success", suggested_actions: [] };

    // Contract target: with v1 interaction and explicit empty suggested_actions,
    // no fallback regex-generated chip should appear from quoted prose.
    expect(extractSuggestions(content, interaction)).toEqual([]);
  });

  it("mapHistoryTurn preserves interaction and snake-case turn-state fields", () => {
    const raw = {
      role: "assistant",
      content: "partial result",
      ts: 1750000000,
      interaction: {
        version: 1,
        turn_state: "partial",
        continue_available: true,
        failure_mode: null,
        request_id: "r1",
      },
      turn_state: "partial",
      continue_available: true,
      failure_mode: null,
    };

    const mapped = mapHistoryTurn(raw);
    expect(mapped.interaction).toEqual(raw.interaction);
    expect(mapped.turn_state).toBe("partial");
    expect(mapped.continue_available).toBe(true);
    expect(mapped.failure_mode).toBe(null);
  });

  it("MessageBubble must not infer review badge from provider/council without interaction evidence", () => {
    render(
      <MemoryRouter>
        <MessageBubble
          idx={0}
          m={{
            role: "assistant",
            content: "Decision summary",
            provider: "mode-b-council",
            council: "A",
            streaming: false,
            interaction: { version: 1, review_evidence: null },
          }}
          sessionId="s1"
          activeProject={{ project_id: "p1" }}
          exhausted={false}
        />
      </MemoryRouter>,
    );

    // Target contract: badge appears only when interaction has real review evidence.
    expect(screen.queryByTestId("council-badge-0")).toBeNull();
  });

  it("interactionFields keeps legacy fallback when interaction envelope is absent", () => {
    const mapped = interactionFields({
      turn_state: "partial",
      continue_available: true,
      failure_mode: "network_drop_mid_stream",
    });
    expect(mapped.turn_state).toBe("partial");
    expect(mapped.continue_available).toBe(true);
    expect(mapped.failure_mode).toBe("network_drop_mid_stream");
  });

  it("hasVerifiedReview requires request/content-bound evidence", () => {
    expect(hasVerifiedReview({
      version: 1,
      request_id: "r1",
      content_hash: "h1",
      review_evidence: {
        status: "passed",
        review_id: "rev1",
        request_id: "r1",
        content_hash: "h1",
        reviewer: "reviewer-2",
      },
    })).toBe(true);

    expect(hasVerifiedReview({
      version: 1,
      request_id: "r1",
      content_hash: "h1",
      review_evidence: {
        status: "passed",
        review_id: "rev1",
        request_id: "r2",
        content_hash: "h2",
      },
    })).toBe(false);
  });

  it("extractSuggestions tolerates malformed suggested_actions without crashing", () => {
    const malformed = {
      version: 1,
      turn_state: "success",
      request_id: "r1",
      suggested_actions: [null, { type: "reply" }, { type: "reply", label: 123 }],
    };
    expect(() => extractSuggestions("irrelevant", malformed)).not.toThrow();
    expect(extractSuggestions("irrelevant", malformed)).toEqual([]);
    expect(extractSuggestions("irrelevant", { ...malformed, suggested_actions: {} })).toEqual([]);
  });

  it("unverified command prose cannot become an actionable suggestion", () => {
    expect(extractSuggestions('Type "git draft" to continue.', {
      version: 1, turn_state: "success", suggested_actions: [],
    })).toEqual([]);
  });

  it("review_mode pro/swift does not imply reviewed badge under v1 without review_evidence", () => {
    render(
      <MemoryRouter>
        <MessageBubble
          idx={1}
          m={{
            role: "assistant",
            content: "Implementation summary",
            provider: "deepseek",
            reviewMode: "pro",
            streaming: false,
            interaction: { version: 1, review_evidence: null },
          }}
          sessionId="s2"
          activeProject={{ project_id: "p2" }}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByText(/reviewed/i)).toBeNull();
    expect(screen.queryByTestId("council-badge-1")).toBeNull();
  });
});
