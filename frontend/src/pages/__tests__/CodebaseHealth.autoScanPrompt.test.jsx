/**
 * CodebaseHealth.autoScanPrompt.test.jsx — Ticket A-UI (2026-02)
 * behavioral RTL tests for the one-time "Turn on weekly auto-scan?"
 * prompt shown after a project's first scan.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), patch: vi.fn() } }));

import { AutoScanPrompt } from "../CodebaseHealth.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: { ok: true, healthy: false, reason: "HTTP 400" } });
});

describe("Ticket A-UI · AutoScanPrompt", () => {
  it("does not render at all when this was NOT the project's first scan", () => {
    const { container } = render(
      <AutoScanPrompt projectId="p1" isFirstScan={false} initialDismissed={false} initialFrequency="off" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("does not render when already dismissed, even on a first-scan response (one-time only)", () => {
    const { container } = render(
      <AutoScanPrompt projectId="p1" isFirstScan={true} initialDismissed={true} initialFrequency="off" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders Off/Weekly/Monthly with Off selected by default", async () => {
    render(<AutoScanPrompt projectId="p1" isFirstScan={true} initialDismissed={false} initialFrequency="off" />);
    expect(screen.getByTestId("auto-scan-prompt")).toBeInTheDocument();
    expect(screen.getByTestId("auto-scan-prompt-off")).toBeChecked();
    expect(screen.getByTestId("auto-scan-prompt-weekly")).not.toBeChecked();
    expect(screen.getByTestId("auto-scan-prompt-monthly")).not.toBeChecked();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
  });

  it("shows the honest 'email delivery isn't configured' warning when Resend is unhealthy (real current state)", async () => {
    render(<AutoScanPrompt projectId="p1" isFirstScan={true} initialDismissed={false} initialFrequency="off" />);
    await waitFor(() => expect(screen.getByTestId("auto-scan-prompt-email-warning")).toBeInTheDocument());
    expect(screen.getByTestId("auto-scan-prompt-email-warning")).toHaveTextContent(
      "Email delivery isn't configured yet — results will appear in-app only.",
    );
  });

  it("never shows the email warning when Resend IS healthy", async () => {
    api.get.mockResolvedValue({ data: { ok: true, healthy: true, reason: null } });
    render(<AutoScanPrompt projectId="p1" isFirstScan={true} initialDismissed={false} initialFrequency="off" />);
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/cto/email-delivery-status"));
    expect(screen.queryByTestId("auto-scan-prompt-email-warning")).toBeNull();
  });

  it("selecting Weekly and clicking Save persists the choice + dismisses, in one PATCH", async () => {
    api.patch.mockResolvedValue({ data: { ok: true } });
    const user = userEvent.setup();
    render(<AutoScanPrompt projectId="p1" isFirstScan={true} initialDismissed={false} initialFrequency="off" />);

    await user.click(screen.getByTestId("auto-scan-prompt-weekly"));
    expect(screen.getByTestId("auto-scan-prompt-weekly")).toBeChecked();
    await user.click(screen.getByTestId("auto-scan-prompt-save"));

    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/cto/projects/p1", {
      auto_scan_prompt_dismissed: true, auto_scan_frequency: "weekly",
    }));
    await waitFor(() => expect(screen.queryByTestId("auto-scan-prompt")).toBeNull());
  });

  it("'Maybe later' dismisses WITHOUT changing the frequency (never silently applies a schedule)", async () => {
    api.patch.mockResolvedValue({ data: { ok: true } });
    const user = userEvent.setup();
    render(<AutoScanPrompt projectId="p1" isFirstScan={true} initialDismissed={false} initialFrequency="off" />);

    await user.click(screen.getByTestId("auto-scan-prompt-monthly")); // change the radio, but don't Save
    await user.click(screen.getByTestId("auto-scan-prompt-dismiss"));

    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/cto/projects/p1", {
      auto_scan_prompt_dismissed: true,
    }));
    await waitFor(() => expect(screen.queryByTestId("auto-scan-prompt")).toBeNull());
  });

  it("a failed save keeps the prompt open so the user can retry (never a silent dismiss on error)", async () => {
    api.patch.mockRejectedValue(new Error("network error"));
    const user = userEvent.setup();
    render(<AutoScanPrompt projectId="p1" isFirstScan={true} initialDismissed={false} initialFrequency="off" />);
    await user.click(screen.getByTestId("auto-scan-prompt-save"));
    await waitFor(() => expect(api.patch).toHaveBeenCalled());
    expect(screen.getByTestId("auto-scan-prompt")).toBeInTheDocument();
  });

  it("is a labelled dialog with a real radiogroup (a11y)", async () => {
    render(<AutoScanPrompt projectId="p1" isFirstScan={true} initialDismissed={false} initialFrequency="off" />);
    expect(screen.getByRole("dialog", { name: "Turn on weekly auto-scan?" })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Auto-scan frequency" })).toBeInTheDocument();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
  });
});
