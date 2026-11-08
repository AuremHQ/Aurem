/**
 * HarnessPage.test.jsx — Aurem Loop harness Phase B.
 * Signed-out teaser, flag-off "not available", full page when flag is
 * on, download success/no-release/error paths, and the honesty
 * constraints (no Ollama/local-model claim, no benchmark-number
 * claim, Loop C zero-real-weight-changes line present).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  api: { post: vi.fn() },
  getToken: vi.fn(),
}));
vi.mock("../../lib/pageFlags", () => ({
  isHarnessPageEnabled: vi.fn(),
}));

import HarnessPage from "../HarnessPage.jsx";
import { api, getToken } from "../../lib/api";
import { isHarnessPageEnabled } from "../../lib/pageFlags";

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
  it("shows the signup CTA when signed out, never the full page", () => {
    getToken.mockReturnValue(null);
    isHarnessPageEnabled.mockReturnValue(true);
    renderPage();
    expect(screen.getByTestId("harness-page-signed-out")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-page")).not.toBeInTheDocument();
    expect(screen.getByTestId("harness-signup-cta")).toBeInTheDocument();
  });

  it("shows 'not available yet' when signed in but the flag is off", () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(false);
    renderPage();
    expect(screen.getByTestId("harness-page-not-available")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-page")).not.toBeInTheDocument();
  });

  it("shows the full page when signed in and the flag is on", () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(true);
    renderPage();
    expect(screen.getByTestId("harness-page")).toBeInTheDocument();
    expect(screen.getByTestId("harness-download-button")).toBeInTheDocument();
  });

  it("never claims Ollama/local-model support (that feature is not_built)", () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(true);
    renderPage();
    const bodyText = document.body.textContent;
    expect(bodyText).not.toMatch(/ollama/i);
    expect(bodyText).not.toMatch(/lm studio/i);
    expect(bodyText).not.toMatch(/llama\.cpp/i);
  });

  it("never claims a specific benchmark pass-rate lift", () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(true);
    renderPage();
    const bodyText = document.body.textContent;
    // No "X/Y" style benchmark fraction and no "% faster/better" claim.
    expect(bodyText).not.toMatch(/\d+\/\d+/);
    expect(bodyText).not.toMatch(/\d+%\s*(faster|better|improvement)/i);
  });

  it("states plainly that Loop C has not produced real weight changes", () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(true);
    renderPage();
    expect(screen.getByTestId("harness-what-it-does-not-do").textContent)
      .toMatch(/not produced any real weight changes/i);
  });

  it("presents exactly one package — free and subscriber both show the local harness as Included", () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(true);
    renderPage();
    expect(screen.getByTestId("harness-tier-free-basic").textContent).toBe("Included");
    expect(screen.getByTestId("harness-tier-sub-basic").textContent).toBe("Included");
    expect(screen.getByTestId("harness-tier-free-cloud").textContent).toBe("Locked");
  });

  it("clicking download calls the real endpoint with a blob response type and saves the file", async () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(true);
    const fakeBlob = new Blob(["fake zip bytes"]);
    api.post.mockResolvedValue({
      data: fakeBlob,
      headers: { "content-disposition": 'attachment; filename="aurem-loop-0.8.0.zip"' },
    });
    renderPage();
    fireEvent.click(screen.getByTestId("harness-download-button"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/harness/download", null, { responseType: "blob" },
    ));
    expect(global.URL.createObjectURL).toHaveBeenCalledWith(fakeBlob);
  });

  it("shows a clean message, never a crash, when no release is published (409)", async () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(true);
    api.post.mockRejectedValue({ response: { status: 409 } });
    renderPage();
    fireEvent.click(screen.getByTestId("harness-download-button"));
    await waitFor(() => expect(screen.getByTestId("harness-no-release")).toBeInTheDocument());
  });

  it("shows a generic error message on any other download failure", async () => {
    getToken.mockReturnValue("fake-jwt");
    isHarnessPageEnabled.mockReturnValue(true);
    api.post.mockRejectedValue({ response: { status: 500 } });
    renderPage();
    fireEvent.click(screen.getByTestId("harness-download-button"));
    await waitFor(() => expect(screen.getByTestId("harness-download-error")).toBeInTheDocument());
  });
});
