/**
 * HarnessPage.test.jsx — Aurem Loop harness, Phase A.
 * Two-stage fetch: GET /harness/page-status (visibility flag) then,
 * only if true, GET /harness/content (the actual copy, served from
 * the private AuremHQ/aurem-loop repo via the backend). Phase A rule:
 * there is NO download CTA anywhere — only a waitlist mailto link,
 * for every visitor regardless of login state.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  healthApi: { get: vi.fn(), post: vi.fn() },
  api: { post: vi.fn() },
  getToken: vi.fn(() => null),
  getUser: vi.fn(() => null),
  isAdminOrFounder: vi.fn(() => false),
}));

import HarnessPage from "../HarnessPage.jsx";
import { api, getToken, healthApi } from "../../lib/api";

const CONTENT = {
  available: true,
  title: "Aurem Loop — local coding harness",
  description: "desc",
  canonical: "https://auremcto.com/harness",
  badges: ["FREE", "LOCAL", "MIT LICENSED"],
  hero: { headline: "Your code. Your PC. Your Accept.", subhead: "sub", body: "body" },
  phaseA: {
    banner: "Not available to download yet.",
    waitlistEmail: "polarisbuiltinc@gmail.com",
    waitlistSubject: "Aurem Loop waitlist",
    waitlistBody: "Please add me.",
  },
  featureTags: ["$0 for local use"],
  demoVideo: { url: "https://example.com/demo.mp4", caption: "Illustration" },
  setApart: {
    headline: "Refine your model.",
    wspKit: { badge: "EXPERIMENTAL", body: "wsp body" },
    phoneTunnel: { badge: "OPTIONAL", body: "tunnel body" },
  },
  githubStats: { repoLabel: "wsp-kit", note: "note", clones: 939, views: 562, lastVerified: "10 Nov 2026" },
  whatItIsNot: ["Not a hosted service."],
  compare: {
    headline: "Compare",
    body: "compare body",
    auremRow: { Price: "$0 (local)" },
    competitorNote: "will be filled in",
  },
  whoShouldNotPick: ["You need frontier models."],
  usersSay: { headline: "What users say", body: "no users yet" },
  setup: { headline: "Setup", steps: ["step one"] },
  troubleshooting: [{ q: "Stuck?", a: "Wait." }],
  faq: [
    { q: "Is it free?", a: "Yes." },
    { q: "Does it send my code anywhere?", a: "No." },
  ],
  footer: {
    tagline: "Aurem Loop tagline",
    legal: "Polaris Built Inc.",
    waitlistEmail: "polarisbuiltinc@gmail.com",
    license: "MIT licensed",
  },
};

const RELEASE = {
  available: true,
  version: "0.8.0",
  platform: "windows",
  sha256: "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
  changelog: ["Initial public Windows build"],
  mandatory: false,
  size_bytes: 292519,
  published_at: 1762819200,
};

function mockEndpoints({ flag, content, liveStats = { stars: 16, forks: 2 }, release = { available: false } }) {
  healthApi.get.mockImplementation((path) => {
    if (path === "/harness/page-status") {
      return Promise.resolve({ data: { harness_page_enabled: flag } });
    }
    if (path === "/harness/content") {
      if (content === "error") return Promise.reject(new Error("network down"));
      return Promise.resolve({ data: { content, live_stats: liveStats } });
    }
    if (path === "/harness/release-status") {
      return Promise.resolve({ data: release });
    }
    return Promise.reject(new Error(`unexpected path ${path}`));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
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
    healthApi.get.mockReturnValue(new Promise(() => {})); // never resolves
    renderPage();
    expect(screen.getByTestId("harness-page-loading")).toBeInTheDocument();
  });

  it("shows 'not available yet' when the flag is off", async () => {
    mockEndpoints({ flag: false, content: CONTENT });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page-not-available")).toBeInTheDocument());
    expect(screen.queryByTestId("harness-page")).not.toBeInTheDocument();
  });

  it("a failed flag-status fetch defaults to hidden, never shows the real page", async () => {
    healthApi.get.mockImplementation((path) => {
      if (path === "/harness/page-status") return Promise.reject(new Error("network down"));
      return Promise.reject(new Error("should not be called"));
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page-not-available")).toBeInTheDocument());
  });

  it("shows 'temporarily unavailable' when the flag is on but content isn't", async () => {
    mockEndpoints({ flag: true, content: { available: false } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page-content-unavailable")).toBeInTheDocument());
  });

  it("shows 'temporarily unavailable' when the content fetch errors", async () => {
    mockEndpoints({ flag: true, content: "error" });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page-content-unavailable")).toBeInTheDocument());
  });

  it("renders the full Phase A page with a real waitlist form and NO download button anywhere", async () => {
    mockEndpoints({ flag: true, content: CONTENT });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(screen.getByTestId("harness-waitlist-form-hero")).toBeInTheDocument();
    expect(screen.getByTestId("harness-waitlist-email-hero")).toBeInTheDocument();
    expect(screen.getByTestId("harness-waitlist-mailto-hero")).toHaveAttribute(
      "href", expect.stringContaining("mailto:polarisbuiltinc@gmail.com"),
    );
    expect(screen.queryByTestId("harness-download-button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("submitting the hero waitlist form posts the email and shows an on-page confirmation", async () => {
    mockEndpoints({ flag: true, content: CONTENT });
    healthApi.post.mockResolvedValue({ data: { ok: true } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("harness-waitlist-email-hero"), { target: { value: "dev@example.com" } });
    fireEvent.click(screen.getByTestId("harness-waitlist-submit-hero"));

    await waitFor(() => expect(screen.getByTestId("harness-waitlist-success-hero")).toBeInTheDocument());
    expect(healthApi.post).toHaveBeenCalledWith("/harness/waitlist", { email: "dev@example.com" });
  });

  it("renders live GitHub stars/forks plus the static clones/views snapshot", async () => {
    mockEndpoints({ flag: true, content: CONTENT, liveStats: { stars: 16, forks: 2 } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(screen.getByTestId("harness-stat-stars")).toHaveTextContent("16");
    expect(screen.getByTestId("harness-stat-forks")).toHaveTextContent("2");
    expect(screen.getByTestId("harness-stat-clones")).toHaveTextContent("939");
    expect(screen.getByTestId("harness-stat-views")).toHaveTextContent("562");
  });

  it("renders exactly as many FAQ entries as the content provides", async () => {
    mockEndpoints({ flag: true, content: CONTENT });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(screen.getByTestId("harness-faq-0")).toBeInTheDocument();
    expect(screen.getByTestId("harness-faq-1")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-faq-2")).not.toBeInTheDocument();
  });

  it("renders the site's real MarketingNav, not a bespoke one", async () => {
    mockEndpoints({ flag: true, content: CONTENT });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(screen.getByTestId("ora-nav")).toBeInTheDocument();
    expect(screen.getByTestId("harness-anchor-nav")).toBeInTheDocument();
  });

  describe("Phase B — only once a real release is published (release-status.available)", () => {
    it("shows the download section with version/SHA-256/changelog and NO Phase A banner, once a release exists", async () => {
      mockEndpoints({ flag: true, content: CONTENT, release: RELEASE });
      renderPage();
      await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
      await waitFor(() => expect(screen.getByTestId("harness-download-section")).toBeInTheDocument());
      expect(screen.getByTestId("harness-download-version")).toHaveTextContent("0.8.0");
      expect(screen.getByTestId("harness-download-sha256")).toHaveTextContent(RELEASE.sha256);
      expect(screen.getByTestId("harness-download-changelog")).toHaveTextContent("Initial public Windows build");
      expect(screen.queryByTestId("harness-phase-a-banner")).not.toBeInTheDocument();
      expect(screen.queryByTestId("harness-waitlist-form-hero")).not.toBeInTheDocument();
    });

    it("a signed-out visitor clicking Download is redirected to signup, no API call made", async () => {
      getToken.mockReturnValue(null);
      mockEndpoints({ flag: true, content: CONTENT, release: RELEASE });
      const originalLocation = window.location;
      delete window.location;
      window.location = { href: "" };
      try {
        renderPage();
        await waitFor(() => expect(screen.getByTestId("harness-download-button")).toBeInTheDocument());
        screen.getByTestId("harness-download-button").click();
        expect(window.location.href).toBe("/signup?next=/harness");
        expect(api.post).not.toHaveBeenCalled();
      } finally {
        window.location = originalLocation;
      }
    });

    it("a signed-in visitor clicking Download calls the authenticated download endpoint and triggers a named file download", async () => {
      getToken.mockReturnValue("fake-jwt");
      api.post.mockResolvedValue({ data: new Blob(["zip-bytes"]) });
      const createObjectURL = vi.fn(() => "blob:mock-url");
      const revokeObjectURL = vi.fn();
      global.URL.createObjectURL = createObjectURL;
      global.URL.revokeObjectURL = revokeObjectURL;
      mockEndpoints({ flag: true, content: CONTENT, release: RELEASE });
      renderPage();
      await waitFor(() => expect(screen.getByTestId("harness-download-button")).toBeInTheDocument());
      screen.getByTestId("harness-download-button").click();
      await waitFor(() => expect(api.post).toHaveBeenCalledWith(
        "/harness/download", {}, { responseType: "blob" },
      ));
      await waitFor(() => expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob)));
      await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url"));
      await waitFor(() => expect(screen.getByTestId("harness-download-button")).not.toBeDisabled());
    });

    it("shows neither the Phase A banner nor the download section while release-status is still resolving (no flash)", async () => {
      healthApi.get.mockImplementation((path) => {
        if (path === "/harness/page-status") return Promise.resolve({ data: { harness_page_enabled: true } });
        if (path === "/harness/content") return Promise.resolve({ data: { content: CONTENT, live_stats: { stars: 16, forks: 2 } } });
        if (path === "/harness/release-status") return new Promise(() => {}); // never resolves
        return Promise.reject(new Error(`unexpected path ${path}`));
      });
      renderPage();
      await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
      expect(screen.queryByTestId("harness-phase-a-banner")).not.toBeInTheDocument();
      expect(screen.queryByTestId("harness-download-section")).not.toBeInTheDocument();
    });

    it("stays on the Phase A waitlist banner when no release is published (current real state)", async () => {
      mockEndpoints({ flag: true, content: CONTENT, release: { available: false } });
      renderPage();
      await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
      expect(screen.getByTestId("harness-phase-a-banner")).toBeInTheDocument();
      expect(screen.getByTestId("harness-waitlist-form-hero")).toBeInTheDocument();
      expect(screen.queryByTestId("harness-download-section")).not.toBeInTheDocument();
    });
  });
});
