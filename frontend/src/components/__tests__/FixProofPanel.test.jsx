/**
 * FixProofPanel.test.jsx — Ticket D-UI (2026-02) behavioral RTL tests.
 * Covers all 4 states (loading/empty/could-not-verify/success), the
 * desktop<->mobile device tab switch, and accessibility (role/aria,
 * alt text, keyboard-focusable tabs).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import { FixProofPanel } from "../FixProofPanel";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  global.URL.createObjectURL = vi.fn(() => "blob:mock-url");
  global.URL.revokeObjectURL = vi.fn();
});

describe("Ticket D-UI · FixProofPanel", () => {
  it("renders nothing when there is no jobId yet", () => {
    const { container } = render(<FixProofPanel jobId={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows a loading state while the meta request is in flight", async () => {
    let resolveFn;
    api.get.mockReturnValue(new Promise((res) => { resolveFn = res; }));
    render(<FixProofPanel jobId="job_1" />);
    expect(screen.getByTestId("fix-proof-loading")).toBeInTheDocument();
    expect(screen.getByTestId("fix-proof-loading")).toHaveAttribute("role", "status");
    resolveFn({ data: { ok: true, attempted: false } });
    await waitFor(() => expect(screen.getByTestId("fix-proof-empty")).toBeInTheDocument());
  });

  it("empty state — never attempted (no preview_url on the project)", async () => {
    api.get.mockResolvedValue({ data: { ok: true, attempted: false } });
    render(<FixProofPanel jobId="job_2" />);
    await waitFor(() => expect(screen.getByTestId("fix-proof-empty")).toBeInTheDocument());
    expect(api.get).toHaveBeenCalledWith("/fix-pipeline/proof/job_2/meta");
    expect(screen.getByTestId("fix-proof-empty")).toHaveTextContent(/wasn.t attempted/i);
    expect(screen.queryByTestId("fix-proof-images")).toBeNull();
  });

  it("could-not-verify state — names the real failure reason, never implies success", async () => {
    api.get.mockResolvedValue({
      data: {
        ok: true, attempted: true,
        devices: {
          desktop: { before_ok: false, after_ok: false, has_images: false,
                     before_fail_reason: "blocked_ssrf", changed: null, diff_percent: null },
          mobile: { before_ok: false, after_ok: false, has_images: false,
                    before_fail_reason: "blocked_ssrf", changed: null, diff_percent: null },
        },
      },
    });
    render(<FixProofPanel jobId="job_3" />);
    await waitFor(() => expect(screen.getByTestId("fix-proof-could-not-verify")).toBeInTheDocument());
    expect(screen.getByTestId("fix-proof-could-not-verify")).toHaveTextContent(/blocked\/internal address/i);
    expect(screen.getByTestId("fix-proof-could-not-verify")).toHaveAttribute("role", "status");
    expect(screen.queryByTestId("fix-proof-images")).toBeNull();
  });

  it("success state — fetches + renders real before/after images with diff %, both devices", async () => {
    api.get.mockImplementation((url) => {
      if (url.includes("/meta")) {
        return Promise.resolve({
          data: {
            ok: true, attempted: true,
            devices: {
              desktop: { before_ok: true, after_ok: true, has_images: true,
                         changed: true, diff_percent: 12.345 },
              mobile: { before_ok: true, after_ok: true, has_images: true,
                        changed: false, diff_percent: 0.0 },
            },
          },
        });
      }
      return Promise.resolve({ data: new Blob(["fake-jpeg"]) });
    });
    const user = userEvent.setup();
    render(<FixProofPanel jobId="job_4" />);

    await waitFor(() => expect(screen.getByTestId("fix-proof-images")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("fix-proof-img-before-desktop")).toBeInTheDocument());
    expect(screen.getByTestId("fix-proof-img-after-desktop")).toBeInTheDocument();
    expect(screen.getByTestId("fix-proof-img-before-desktop")).toHaveAttribute("alt", "Before screenshot (desktop)");
    expect(screen.getByTestId("fix-proof-diff-percent")).toHaveTextContent("12.3% visual difference");

    expect(api.get).toHaveBeenCalledWith(
      "/fix-pipeline/proof/job_4?variant=before&device=desktop", { responseType: "blob" },
    );
    expect(api.get).toHaveBeenCalledWith(
      "/fix-pipeline/proof/job_4?variant=after&device=desktop", { responseType: "blob" },
    );

    const mobileTab = screen.getByTestId("fix-proof-device-mobile");
    await user.click(mobileTab);
    await waitFor(() => expect(screen.getByTestId("fix-proof-img-before-mobile")).toBeInTheDocument());
    expect(screen.getByTestId("fix-proof-diff-percent")).toHaveTextContent("No visual change detected");
    expect(api.get).toHaveBeenCalledWith(
      "/fix-pipeline/proof/job_4?variant=before&device=mobile", { responseType: "blob" },
    );
  });

  it("device tabs are a real tablist with keyboard-reachable, labelled tabs (a11y)", async () => {
    api.get.mockResolvedValue({
      data: { ok: true, attempted: true,
              devices: { desktop: { has_images: false, before_fail_reason: "timeout" },
                         mobile: { has_images: false, before_fail_reason: "timeout" } } },
    });
    render(<FixProofPanel jobId="job_5" />);
    await waitFor(() => expect(screen.getByRole("tablist", { name: "Device" })).toBeInTheDocument());
    const tablist = screen.getByRole("tablist", { name: "Device" });
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs).toHaveLength(2);
    expect(screen.getByRole("tab", { name: "Desktop view" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Mobile view" })).toHaveAttribute("aria-selected", "false");
    const user = userEvent.setup();
    await user.tab();
    await user.keyboard("{Enter}");
    await user.tab();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("tab", { name: "Mobile view" }))
      .toHaveAttribute("aria-selected", "true"));
  });

  it("meta fetch failure degrades to the empty state honestly (never a crash, never a fake success)", async () => {
    api.get.mockRejectedValue(new Error("network error"));
    render(<FixProofPanel jobId="job_6" />);
    await waitFor(() => expect(screen.getByTestId("fix-proof-empty")).toBeInTheDocument());
  });
});
