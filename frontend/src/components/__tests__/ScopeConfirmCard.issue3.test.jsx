import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, it, expect, vi } from "vitest";
vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));
import { api } from "../../lib/api";
import ScopeConfirmCard from "../ScopeConfirmCard";

beforeEach(() => { vi.clearAllMocks(); api.get.mockResolvedValue({ data: { proposal: { status: "pending" } } }); });
const renderCard = (props = {}) => render(<ScopeConfirmCard proposalId="p1" prompt="original" {...props} />);

it.each([undefined, { ok: false }, {}, { status: "succeeded", proposal_id: "p1" }])("never claims success for malformed/rejected callback %s", async (value) => {
  renderCard({ onConfirm: vi.fn().mockResolvedValue(value) });
  fireEvent.click(screen.getByRole("button", { name: "Confirm and proceed" }));
  await screen.findByRole("alert");
  expect(screen.queryByText(/Confirmed — proceeding|Commit recorded/)).toBeNull();
  expect(screen.getByRole("button", { name: "Confirm and proceed" })).not.toBeDisabled();
});

it("waits for the real callback receipt and blocks double clicks", async () => {
  let complete;
  const confirm = vi.fn(() => new Promise((resolve) => { complete = resolve; }));
  renderCard({ onConfirm: confirm });
  const button = screen.getByRole("button", { name: "Confirm and proceed" });
  fireEvent.click(button); fireEvent.click(button);
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("scope-confirm-status-p1")).toHaveTextContent("Waiting for the execution receipt");
  complete({ proposal_id: "p1", status: "succeeded", task_id: "t1", commit_sha: "a".repeat(40) });
  await waitFor(() => expect(screen.getByTestId("scope-confirm-status-p1")).toHaveTextContent("Commit recorded"));
  expect(screen.getByTestId("scope-confirm-task-id-p1")).toHaveTextContent("t1");
});

it("shows rejected callback error without a false confirmation", async () => {
  renderCard({ onConfirm: vi.fn().mockRejectedValue(new Error("Scope expired")) });
  fireEvent.click(screen.getByRole("button", { name: "Confirm and proceed" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Scope expired");
});

it("does not call an executing/unknown receipt completed", async () => {
  renderCard({ onConfirm: vi.fn().mockResolvedValue({ proposal_id: "p1", status: "unknown", task_id: "t1" }) });
  fireEvent.click(screen.getByRole("button", { name: "Confirm and proceed" }));
  await waitFor(() => expect(screen.getByTestId("scope-confirm-status-p1")).toHaveTextContent("Execution not yet verified"));
});

it("does not claim cancellation when cancellation fails", async () => {
  renderCard({ onCancel: vi.fn().mockRejectedValue(new Error("network")) });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("Proposal cancelled.")).toBeNull();
});

it("recovers a persisted commit receipt on reload", async () => {
  api.get.mockResolvedValue({ data: { proposal: { execution_receipt: {
    proposal_id: "p1", status: "succeeded", task_id: "t1", commit_sha: "b".repeat(40),
  } } } });
  renderCard();
  await waitFor(() => expect(screen.getByTestId("scope-confirm-status-p1")).toHaveTextContent("Commit recorded"));
  expect(screen.queryByRole("button", { name: "Confirm and proceed" })).toBeNull();
});

it("updates the original proposal card when typed confirmation returns a receipt", async () => {
  const { rerender } = renderCard();
  rerender(<ScopeConfirmCard proposalId="p1" executionReceipt={{
    proposal_id: "p1", status: "succeeded", task_id: "t1", commit_sha: "c".repeat(40),
  }} />);
  await waitFor(() => expect(screen.getByTestId("scope-confirm-status-p1")).toHaveTextContent("Commit recorded"));
  expect(screen.queryByRole("button", { name: "Confirm and proceed" })).toBeNull();
});