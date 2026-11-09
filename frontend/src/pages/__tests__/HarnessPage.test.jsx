/**
 * HarnessPage.test.jsx — Aurem Loop harness Phase B (redesigned).
 * Flag checked via the public GET /harness/page-status (not
 * pageFlags.js, which only resolves after login — the pre-login gap
 * this redesign closes). Flag off -> hidden for everyone. Flag on ->
 * full page for everyone; only the CTA button differs by login state.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  api: { post: vi.fn() },
  healthApi: { get: vi.fn() },
  getToken: vi.fn(),
  getUser: vi.fn(() => null),
  isAdminOrFounder: vi.fn(() => false),
}));

import HarnessPage from "../HarnessPage.jsx";
import { api, healthApi, getToken } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  global.URL.createObjectURL = vi.fn(() => "blob:fake-url");
  global.URL.revokeObjectURL = vi.fn();
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/harness"]}>
      <Routes>
        <Route path="/harness" element={<HarnessPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("HarnessPage", () => {
  it("shows a loading state before the flag check resolves", () => {
    getToken.mockReturnValue(null);
    healthApi.get.mockReturnValue(new Promise(() => {})); // never resolves
    renderPage();
    expect(screen.getByTestId("harness-page-loading")).toBeInTheDocument();
  });

  it("shows 'not available yet' for EVERYONE when the flag is off, signed out", async () => {
    getToken.mockReturnValue(null);
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page-not-available")).toBeInTheDocument());
    expect(screen.queryByTestId("harness-page")).not.toBeInTheDocument();
  });

  it("shows 'not available yet' for EVERYONE when the flag is off, signed in", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page-not-available")).toBeInTheDocument());
  });

  it("a failed flag-status fetch defaults to hidden, never shows the real page", async () => {
    getToken.mockReturnValue(null);
    healthApi.get.mockRejectedValue(new Error("network down"));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page-not-available")).toBeInTheDocument());
  });

  it("shows the full page to a SIGNED-OUT visitor when the flag is on, with a signup CTA", async () => {
    getToken.mockReturnValue(null);
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(screen.getByTestId("harness-signup-cta")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-download-button")).not.toBeInTheDocument();
  });

  it("shows the full page to a SIGNED-IN user when the flag is on, with a download button", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(screen.getByTestId("harness-download-button")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-signup-cta")).not.toBeInTheDocument();
  });

  it("renders exactly 7 FAQ entries", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    for (let i = 0; i < 7; i++) {
      expect(screen.getByTestId(`harness-faq-${i}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId("harness-faq-7")).not.toBeInTheDocument();
  });

  it("renders the site's real nav, not a bespoke one", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(document.querySelector(".aurem-marketing-nav")).toBeTruthy();
  });

  it("renders a footer with About/Contact/Privacy links", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-footer")).toBeInTheDocument());
    const footer = screen.getByTestId("harness-footer");
    expect(footer.textContent).toContain("About");
    expect(footer.textContent).toContain("Contact");
    expect(footer.textContent).toContain("Privacy");
  });

  it("never claims Ollama/local-model support anywhere on the page", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    const bodyText = document.body.textContent;
    expect(bodyText).not.toMatch(/ollama/i);
    expect(bodyText).not.toMatch(/lm studio/i);
    expect(bodyText).not.toMatch(/llama\.cpp/i);
  });

  it("never claims a specific benchmark pass-rate", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    const bodyText = document.body.textContent;
    expect(bodyText).not.toMatch(/\d+\/\d+/);
    expect(bodyText).not.toMatch(/\d+%\s*(faster|better|improvement)/i);
  });

  it("states plainly that Loop C has not produced real weight changes", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-what-it-does-not-do")).toBeInTheDocument());
    expect(screen.getByTestId("harness-what-it-does-not-do").textContent)
      .toMatch(/not produced any real weight changes/i);
  });

  it("shows the cloud option as Locked for free, never as working", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-tiers")).toBeInTheDocument());
    expect(screen.getByTestId("harness-tier-free-1").textContent).toBe("Locked");
  });

  it("clicking download (signed in) calls the real endpoint and saves the blob", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    const fakeBlob = new Blob(["fake zip bytes"]);
    api.post.mockResolvedValue({
      data: fakeBlob,
      headers: { "content-disposition": 'attachment; filename="aurem-loop-0.8.0.zip"' },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-download-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-download-button"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/harness/download", null, { responseType: "blob" },
    ));
    expect(global.URL.createObjectURL).toHaveBeenCalledWith(fakeBlob);
  });

  it("shows a clean message, never a crash, when no release is published (409)", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    api.post.mockRejectedValue({ response: { status: 409 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-download-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-download-button"));
    await waitFor(() => expect(screen.getByTestId("harness-no-release")).toBeInTheDocument());
  });

  it("shows a generic error message on any other download failure", async () => {
    getToken.mockReturnValue("fake-jwt");
    healthApi.get.mockResolvedValue({ data: { harness_page_enabled: true } });
    api.post.mockRejectedValue({ response: { status: 500 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-download-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-download-button"));
    await waitFor(() => expect(screen.getByTestId("harness-download-error")).toBeInTheDocument());
  });
});
