/**
 * CodebaseHealth.downloadPdf.test.jsx — Ticket C-UI (2026-02)
 * behavioral RTL tests for the "Download PDF" button next to the
 * scan report / health card on the Codebase Health page.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import { DownloadPdfButton } from "../CodebaseHealth.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  global.URL.createObjectURL = vi.fn(() => "blob:mock-url");
  global.URL.revokeObjectURL = vi.fn();
});

describe("Ticket C-UI · DownloadPdfButton", () => {
  it("calls the owner-only report.pdf endpoint as a blob and triggers a real download", async () => {
    api.get.mockResolvedValue({ data: new Blob(["fake-pdf"], { type: "application/pdf" }) });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const user = userEvent.setup();
    render(<DownloadPdfButton projectId="proj_1" />);

    const btn = screen.getByTestId("health-pdf-download-btn");
    expect(btn).not.toBeDisabled();
    await user.click(btn);

    await waitFor(() => expect(api.get).toHaveBeenCalledWith(
      "/cto/projects/proj_1/report.pdf", { responseType: "blob" },
    ));
    expect(clickSpy).toHaveBeenCalled();
    await waitFor(() => expect(btn).not.toBeDisabled());
    clickSpy.mockRestore();
  });

  it("disables the button and shows a spinner while generating", async () => {
    let resolveFn;
    api.get.mockReturnValue(new Promise((res) => { resolveFn = res; }));
    const user = userEvent.setup();
    render(<DownloadPdfButton projectId="proj_1" />);

    const btn = screen.getByTestId("health-pdf-download-btn");
    await user.click(btn);
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
    expect(btn).toHaveTextContent(/generating/i);

    resolveFn({ data: new Blob(["fake-pdf"]) });
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it("401 → honest 'not logged in' message, never a silent failure", async () => {
    api.get.mockRejectedValue({ response: { status: 401 } });
    const user = userEvent.setup();
    render(<DownloadPdfButton projectId="proj_1" />);
    await user.click(screen.getByTestId("health-pdf-download-btn"));
    await waitFor(() => expect(screen.getByTestId("health-pdf-download-error")).toBeInTheDocument());
    expect(screen.getByTestId("health-pdf-download-error")).toHaveTextContent(/sign in again/i);
    expect(screen.getByTestId("health-pdf-download-error")).toHaveAttribute("role", "alert");
  });

  it("403 → honest 'no access' message", async () => {
    api.get.mockRejectedValue({ response: { status: 403 } });
    const user = userEvent.setup();
    render(<DownloadPdfButton projectId="proj_1" />);
    await user.click(screen.getByTestId("health-pdf-download-btn"));
    expect(await screen.findByText(/don.t have access/i)).toBeInTheDocument();
  });

  it("404 → honest 'project not found' message", async () => {
    api.get.mockRejectedValue({ response: { status: 404 } });
    const user = userEvent.setup();
    render(<DownloadPdfButton projectId="proj_1" />);
    await user.click(screen.getByTestId("health-pdf-download-btn"));
    expect(await screen.findByText(/project not found/i)).toBeInTheDocument();
  });

  it("generic/network error → fallback honest message, button re-enables for retry", async () => {
    api.get.mockRejectedValue(new Error("network down"));
    const user = userEvent.setup();
    render(<DownloadPdfButton projectId="proj_1" />);
    const btn = screen.getByTestId("health-pdf-download-btn");
    await user.click(btn);
    expect(await screen.findByText(/could not generate the report/i)).toBeInTheDocument();
    expect(btn).not.toBeDisabled();
  });
});
