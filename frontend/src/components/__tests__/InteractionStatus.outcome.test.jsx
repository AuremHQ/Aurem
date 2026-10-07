import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { InteractionStatus } from "../InteractionStatus";

const show = (interaction) => {
  render(<InteractionStatus interaction={{ version: 1, ...interaction }} index={0} />);
  return screen.getByTestId("interaction-status-0");
};

describe("InteractionStatus follows the evidence-based outcome", () => {
  it("never says 'Response delivered' when nothing was done", () => {
    const el = show({ turn_state: "success", outcome: "NOTHING_DONE", reason: "announced_not_done" });
    expect(el.textContent).toBe("Not done yet — nothing was changed");
    expect(el.textContent).not.toMatch(/delivered/i);
  });

  it("labels a plain answer as answered, not as work", () => {
    expect(show({ turn_state: "success", outcome: "NOTHING_DONE", reason: "answered" }).textContent)
      .toBe("Answered — no action taken");
  });

  it("shows Done with the tool evidence on hover", () => {
    const el = show({ turn_state: "success", outcome: "DONE", reason: "tool_result", evidence: ["read_repo_file"] });
    expect(el.textContent).toBe("Done");
    expect(el.getAttribute("title")).toBe("read_repo_file");
  });

  it("shows a waiting change as needing the user", () => {
    expect(show({ turn_state: "success", outcome: "NEEDS_YOU", reason: "change_awaiting_approval" }).textContent)
      .toBe("Waiting for you");
  });

  it("falls back to the old labels for envelopes without an outcome", () => {
    expect(show({ turn_state: "partial" }).textContent).toBe("Incomplete");
  });
});
