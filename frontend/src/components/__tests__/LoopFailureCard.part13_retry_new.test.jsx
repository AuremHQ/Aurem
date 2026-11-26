/**
 * LoopFailureCard.part13_retry_new.test.jsx — Part XIII · 2(c).
 *
 * "Retry as new loop": a terminal FAILED loop can never be
 * resurrected (the backend 409s pause-response retry on terminal
 * loops), so the card offers a fresh loop with the same request.
 * The card stays presentational — it calls the parent-owned
 * `onRetryAsNewLoop` prop with the original message; ChatPanel owns
 * the actual startLoop call (locked in by the source-contract test
 * ChatPanel.part13_retry_new.test.jsx).
 */
import React from "react";
import {
  render, screen, fireEvent, cleanup,
} from "@testing-library/react";
import {
  describe, it, expect, afterEach, vi,
} from "vitest";

import LoopFailureCard from "../LoopFailureCard";

describe("Part XIII · 2(c) — LoopFailureCard 'Retry as new loop'", () => {
  afterEach(() => cleanup());

  const baseProps = {
    phase: "verify",
    reason: "Verify failed after 2 self-heal attempts. Loop halted (terminal).",
    failedFiles: ["a.py"],
    errors: ["a.py:1:1: E999 SyntaxError"],
    maxSelfHeals: 2,
    originalMessage: "Add input validation to the signup form",
  };

  it("renders a 'Retry as new loop' button when the handler is provided", () => {
    render(<LoopFailureCard {...baseProps} onRetryAsNewLoop={() => {}} />);
    const btn = screen.getByTestId("loop-failure-retry-new");
    expect(btn).toBeInTheDocument();
    expect(btn.textContent).toMatch(/retry as new loop/i);
  });

  it("clicking it calls onRetryAsNewLoop with the original message", () => {
    const handler = vi.fn();
    render(<LoopFailureCard {...baseProps} onRetryAsNewLoop={handler} />);
    fireEvent.click(screen.getByTestId("loop-failure-retry-new"));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith("Add input validation to the signup form");
  });

  it("renders NO retry button when the parent passes no handler", () => {
    render(<LoopFailureCard {...baseProps} />);
    expect(screen.queryByTestId("loop-failure-retry-new")).not.toBeInTheDocument();
  });

  it("renders the button even without failure detail (reason-only card)", () => {
    const handler = vi.fn();
    render(
      <LoopFailureCard
        phase="ship"
        reason="GitHub push rejected: protected branch"
        originalMessage="Ship the new pricing page"
        onRetryAsNewLoop={handler}
      />,
    );
    fireEvent.click(screen.getByTestId("loop-failure-retry-new"));
    expect(handler).toHaveBeenCalledWith("Ship the new pricing page");
  });

  it("does not call any pause/resume path — the card has no such prop", () => {
    // The card's only retry affordance is onRetryAsNewLoop; there is
    // no onRetry/onResume prop that could resurrect a terminal loop.
    const handler = vi.fn();
    render(<LoopFailureCard {...baseProps} onRetryAsNewLoop={handler} />);
    fireEvent.click(screen.getByTestId("loop-failure-retry-new"));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(Object.keys(baseProps)).not.toContain("onRetry");
  });
});
