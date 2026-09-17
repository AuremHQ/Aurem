/**
 * PreviewPanel.reachability_2026_09_17.test.jsx
 *
 * Founder-reported: the "Live now" iframe can render a permanently
 * blank/white result with zero explanation when the underlying
 * deployment doesn't exist or is down. Root cause: the panel only
 * ever inferred up/down state from the iframe's own `onLoad` event,
 * which fires even for a browser-level connection-error page (a
 * false "loaded" signal), hiding the "may be offline" overlay.
 *
 * Fix: a real server-side reachability probe
 * (`GET /cto/projects/{id}/preview/reachability`) now takes priority
 * over the unreliable `onLoad` signal whenever it comes back
 * negative, and distinguishes "no_deployment" (nothing listening at
 * all) from "deployment_down" (a server responded, just with a 5xx).
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

const mockGet = vi.fn();
vi.mock("../../lib/api", () => ({
  api: { get: (...args) => mockGet(...args), post: vi.fn(async () => ({ data: {} })) },
  getToken: () => null,
}));

import PreviewPanel from "../PreviewPanel.jsx";

describe("PreviewPanel — reachability-driven live-preview overlay", () => {
  it("shows a clear 'no deployment' message when reachability says no_deployment, even though onLoad fires", async () => {
    mockGet.mockImplementation((url) => {
      if (url.includes("/preview/reachability")) {
        return Promise.resolve({
          data: {
            ok: true, reachable: false, reason: "no_deployment",
            detail: "Couldn't connect to https://dead.example.com — nothing appears to be listening at this address.",
            checked_url: "https://dead.example.com",
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <PreviewPanel
        blocks={[]}
        activeProject={{ project_id: "pt_x1", live_url: "https://dead.example.com" }}
        onClose={() => {}}
      />,
    );

    const iframe = screen.getByTestId("preview-iframe-live");
    // Simulate the browser firing onLoad for a connection-error page —
    // this used to hide the overlay entirely (the bug).
    fireEvent.load(iframe);

    await waitFor(() => {
      expect(screen.getByTestId("preview-live-unreachable-title").textContent)
        .toBe("No live deployment reachable");
    });
    expect(screen.getByTestId("preview-live-state-overlay").textContent)
      .toContain("Nothing appears to be listening");
  });

  it("shows a distinct 'deployment down' message (not 'no deployment') for a 5xx-style reason", async () => {
    mockGet.mockImplementation((url) => {
      if (url.includes("/preview/reachability")) {
        return Promise.resolve({
          data: {
            ok: true, reachable: false, reason: "deployment_down",
            detail: "https://broken.example.com responded with HTTP 502.",
            checked_url: "https://broken.example.com", status_code: 502,
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <PreviewPanel
        blocks={[]}
        activeProject={{ project_id: "pt_x2", live_url: "https://broken.example.com" }}
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("preview-live-unreachable-title").textContent)
        .toBe("This deployment isn't responding");
    });
    expect(screen.getByTestId("preview-live-state-overlay").textContent)
      .toContain("likely exists but is unhealthy");
  });

  it("does NOT show the unreachable overlay when reachability says reachable — normal onLoad flow still works", async () => {
    mockGet.mockImplementation((url) => {
      if (url.includes("/preview/reachability")) {
        return Promise.resolve({
          data: { ok: true, reachable: true, status_code: 200, checked_url: "https://ok.example.com" },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <PreviewPanel
        blocks={[]}
        activeProject={{ project_id: "pt_x3", live_url: "https://ok.example.com" }}
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.stringContaining("/preview/reachability"),
      );
    });

    const iframe = screen.getByTestId("preview-iframe-live");
    fireEvent.load(iframe);

    await waitFor(() => {
      expect(screen.queryByTestId("preview-live-state-overlay")).toBeNull();
    });
    expect(screen.queryByTestId("preview-live-unreachable-title")).toBeNull();
  });

  it("Retry re-runs the reachability check", async () => {
    let calls = 0;
    mockGet.mockImplementation((url) => {
      if (url.includes("/preview/reachability")) {
        calls += 1;
        return Promise.resolve({
          data: {
            ok: true, reachable: false, reason: "no_deployment",
            detail: "no", checked_url: "https://dead.example.com",
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <PreviewPanel
        blocks={[]}
        activeProject={{ project_id: "pt_x4", live_url: "https://dead.example.com" }}
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("preview-live-retry-btn")).toBeTruthy();
    });
    const callsBeforeRetry = calls;
    fireEvent.click(screen.getByTestId("preview-live-retry-btn"));

    await waitFor(() => {
      expect(calls).toBeGreaterThan(callsBeforeRetry);
    });
  });
});
