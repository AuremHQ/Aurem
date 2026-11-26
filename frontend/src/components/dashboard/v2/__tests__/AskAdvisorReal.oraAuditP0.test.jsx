/**
 * AskAdvisorReal.oraAuditP0.test.jsx — ORA audit P0 (claim #4).
 *
 * Two defects, both fixed:
 *   1. Every advisor turn hardcoded `session_id: null`, so the
 *      backend could never attach turns to a session and multi-turn
 *      context was impossible. Fix: send Shell's real session id
 *      (SessionCtx) with every turn.
 *   2. The only [effectiveProjectId] effect refetched /advisor/context
 *      but never wiped the local messages, so a project switch left
 *      the previous project's conversation on screen. Fix: reset
 *      messages to the fresh welcome (and stop any in-flight reply)
 *      whenever the effective project changes.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import AskAdvisorReal from "../AskAdvisorReal.jsx";

const streamChatMock = vi.fn();
vi.mock("../../../../lib/api", () => ({
  api: { get: vi.fn().mockResolvedValue({ data: { project_name: "P", findings: { total: 0 } } }) },
  streamChat: (...args) => streamChatMock(...args),
}));
vi.mock("../../../Shell", () => ({
  useChatSession: () => ({ sessionId: "s_real_123" }),
}));
vi.mock("../../../TabBar", () => ({
  getActiveProjectId: () => "p1",
}));
vi.mock("html2canvas", () => ({
  default: vi.fn().mockResolvedValue({ toDataURL: () => "data:image/png;base64,xxx" }),
}));
vi.mock("../cn", () => ({ cn: (...xs) => xs.filter(Boolean).join(" ") }));
vi.mock("../../../../lib/zIndex", () => ({ Z_ADVISOR_TOGGLE: 100 }));
vi.mock("../../../CharCounter", () => ({ CharCounter: () => null }));
vi.mock("../../../../utils/chatTextUtils", () => ({ preventSmartPunctuation: () => {} }));

describe("P0 claim #4 · AskAdvisorReal sends a real session id", () => {
  beforeEach(() => {
    streamChatMock.mockClear();
    // jsdom has neither matchMedia nor Element.scrollTo; the panel
    // uses both (responsive width var + auto-scroll on new turns).
    window.matchMedia = window.matchMedia || vi.fn().mockImplementation((q) => ({
      matches: false,
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
    }));
    Element.prototype.scrollTo = Element.prototype.scrollTo || vi.fn();
  });

  it("sends Shell's session id (not null) with project_id on every turn", async () => {
    render(<AskAdvisorReal projectId="p1" />);
    fireEvent.change(screen.getByTestId("ds2-advisor-input"), {
      target: { value: "why did the deploy fail?" },
    });
    fireEvent.click(screen.getByTestId("ds2-advisor-send"));

    await waitFor(() => expect(streamChatMock).toHaveBeenCalledTimes(1));
    expect(streamChatMock).toHaveBeenCalledWith(expect.objectContaining({
      session_id: "s_real_123",
      project_id: "p1",
      ora_panel: true,
    }));
    // And the turn is NOT sent with the old hardcoded null.
    expect(streamChatMock.mock.calls[0][0].session_id).not.toBeNull();
  });

  it("switching projects wipes the previous project's advisor conversation", async () => {
    const { rerender } = render(<AskAdvisorReal projectId="p1" />);
    fireEvent.change(screen.getByTestId("ds2-advisor-input"), {
      target: { value: "old project question" },
    });
    fireEvent.click(screen.getByTestId("ds2-advisor-send"));
    await waitFor(() => expect(streamChatMock).toHaveBeenCalledTimes(1));

    // The old project's turn is on screen.
    expect(screen.getByText("old project question")).toBeInTheDocument();

    // Switch projects: the conversation must reset to the welcome.
    rerender(<AskAdvisorReal projectId="p2" />);
    await waitFor(() => {
      expect(screen.queryByText("old project question")).not.toBeInTheDocument();
    });
    expect(screen.getAllByText(/Hi — I'm the ORA Advisor/).length).toBe(1);
  });
});
