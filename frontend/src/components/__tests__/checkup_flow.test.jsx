import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/checkupApi", async () => {
  const actual = await vi.importActual("../../lib/checkupApi");
  return {
    ...actual,
    fetchCheckupFlags: vi.fn(),
    fetchLatest: vi.fn(),
    streamCheckup: vi.fn(),
    ignoreItem: vi.fn(async () => true),
    answerQuestion: vi.fn(async () => true),
    skipCheckup: vi.fn(async () => null),
  };
});

import * as api from "../../lib/checkupApi";
import CheckupHost from "../checkup/CheckupHost";
import { resetCheckupFlagsCache } from "../../hooks/useCheckupFlags";

const ITEM = { id: "a", stage: "safe", severity: "critical", title_plain: "A master key is visible to every visitor",
               impact_plain: "Anyone can copy it." };
const DONE = {
  type: "done", score: 44, items: [ITEM],
  stages: [{ stage: "runs", verdict: "ok", open_count: 0 }, { stage: "safe", verdict: "problem", open_count: 1 },
           { stage: "permissions", verdict: "ok", open_count: 0 }, { stage: "launch", verdict: "ok", open_count: 0 }],
  unverified: [{ stage: "launch", check: "x", reason: "r" }],
  permissions: { supported: true, questions: [{ table: "gadgets", text: "Should anyone on the internet be able to see your gadgets list?",
    options: [{ value: "public", label: "Yes, it's public" }, { value: "private", label: "No, keep it private" }] }],
    matrix: [{ name: "Gadgets", visitors: "read", signed_in: "read", developer: { table: "gadgets", rls: true } }] },
};

beforeEach(() => {
  resetCheckupFlagsCache();
  localStorage.clear();
  vi.clearAllMocks();
  api.fetchLatest.mockResolvedValue({ checkup: null, items: [] });
  api.streamCheckup.mockImplementation(async (_p, { onEvent }) => {
    onEvent({ type: "stage_started", stage: "runs" });
    onEvent(DONE);
  });
});

describe("CheckupHost", () => {
  it("renders nothing and never calls the run endpoint when the flag is off", async () => {
    api.fetchCheckupFlags.mockResolvedValue({ checkup_flow: false });
    const { container } = render(<CheckupHost projectId="p" />);
    await waitFor(() => expect(api.fetchCheckupFlags).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
    expect(api.streamCheckup).not.toHaveBeenCalled();
  });

  it("auto-runs on first visit, shows plain-English verdict, never raw ids", async () => {
    api.fetchCheckupFlags.mockResolvedValue({ checkup_flow: true, fix_list_panel: true });
    render(<CheckupHost projectId="p" />);
    expect(await screen.findByTestId("checkup-score")).toHaveTextContent("44");
    expect(screen.getByTestId("checkup-top-items")).toHaveTextContent("A master key is visible");
    expect(screen.getByTestId("checkup-unverified")).toHaveTextContent("Couldn't verify 1 check");
    expect(screen.getByTestId("checkup-stage-safe")).toHaveTextContent("1 to look at");
  });

  it("Open chat anyway closes the screen and leaves a pinned bar", async () => {
    api.fetchCheckupFlags.mockResolvedValue({ checkup_flow: true, fix_list_panel: true });
    render(<CheckupHost projectId="p" />);
    fireEvent.click(await screen.findByTestId("checkup-open-chat"));
    expect(screen.queryByTestId("checkup-screen")).toBeNull();
    expect(screen.getByTestId("checkup-pinned-bar")).toHaveTextContent("44/100");
  });

  it("answering a question saves it and re-runs the checkup", async () => {
    api.fetchCheckupFlags.mockResolvedValue({ checkup_flow: true });
    render(<CheckupHost projectId="p" />);
    fireEvent.click(await screen.findByText("Yes, it's public"));
    await waitFor(() => expect(api.answerQuestion).toHaveBeenCalledWith("p", "gadgets", "public"));
    await waitFor(() => expect(api.streamCheckup).toHaveBeenCalledTimes(2));
  });

  it("a failed run shows a friendly message with an escape hatch", async () => {
    api.fetchCheckupFlags.mockResolvedValue({ checkup_flow: true });
    api.streamCheckup.mockImplementation(async (_p, { onEvent }) => onEvent({ type: "error", message: "We couldn't read your code right now. Your chat still works." }));
    render(<CheckupHost projectId="p" />);
    expect(await screen.findByText(/Your chat still works/)).toBeInTheDocument();
    expect(screen.getByTestId("checkup-open-chat")).toBeInTheDocument();
  });

  it("Escape and ✕ both close the screen", async () => {
    api.fetchCheckupFlags.mockResolvedValue({ checkup_flow: true });
    render(<CheckupHost projectId="p" />);
    fireEvent.click(await screen.findByTestId("checkup-close"));
    expect(screen.queryByTestId("checkup-screen")).toBeNull();
  });

  it("does not re-run when a checkup already exists, and the fix list ignores items", async () => {
    api.fetchCheckupFlags.mockResolvedValue({ checkup_flow: true, fix_list_panel: true });
    api.fetchLatest.mockResolvedValue({ checkup: { score: 44 }, items: [ITEM], score: 44, stages: [] });
    render(<CheckupHost projectId="p" />);
    fireEvent.click(await screen.findByTestId("checkup-pinned-bar"));
    expect(api.streamCheckup).not.toHaveBeenCalled();
    expect(await screen.findAllByTestId("fix-list-item")).toHaveLength(1);
    fireEvent.click(screen.getByText("Ignore"));
    await waitFor(() => expect(screen.getByTestId("fix-list-empty")).toBeInTheDocument());
    expect(api.ignoreItem).toHaveBeenCalledWith("p", "a");
  });

  it("Ask ORA to help prefills chat via the existing event", async () => {
    api.fetchCheckupFlags.mockResolvedValue({ checkup_flow: true, fix_list_panel: true });
    api.fetchLatest.mockResolvedValue({ checkup: { score: 44 }, items: [ITEM], score: 44, stages: [] });
    const seen = vi.fn();
    window.addEventListener("aurem:starter-pick", seen);
    render(<CheckupHost projectId="p" />);
    fireEvent.click(await screen.findByTestId("checkup-pinned-bar"));
    fireEvent.click(await screen.findByText("Ask ORA to help"));
    expect(seen.mock.calls[0][0].detail.prompt).toContain("A master key is visible");
    window.removeEventListener("aurem:starter-pick", seen);
  });
});

describe("parseSseFrames", () => {
  it("parses complete frames and keeps the partial remainder", async () => {
    const { parseSseFrames } = await vi.importActual("../../lib/checkupApi");
    const { events, rest } = parseSseFrames('data: {"type":"a"}\n\ndata: {"type":"b"}\n\ndata: {"ty');
    expect(events.map((e) => e.type)).toEqual(["a", "b"]);
    expect(rest).toBe('data: {"ty');
  });
});
