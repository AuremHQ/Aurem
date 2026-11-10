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
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  healthApi: { get: vi.fn() },
  getToken: vi.fn(() => null),
  getUser: vi.fn(() => null),
  isAdminOrFounder: vi.fn(() => false),
}));

import HarnessPage from "../HarnessPage.jsx";
import { healthApi } from "../../lib/api";

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

function mockEndpoints({ flag, content, liveStats = { stars: 16, forks: 2 } }) {
  healthApi.get.mockImplementation((path) => {
    if (path === "/harness/page-status") {
      return Promise.resolve({ data: { harness_page_enabled: flag } });
    }
    if (path === "/harness/content") {
      if (content === "error") return Promise.reject(new Error("network down"));
      return Promise.resolve({ data: { content, live_stats: liveStats } });
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

  it("renders the full Phase A page with a waitlist CTA and NO download button anywhere", async () => {
    mockEndpoints({ flag: true, content: CONTENT });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("harness-page")).toBeInTheDocument());
    expect(screen.getByTestId("harness-waitlist-cta")).toHaveAttribute(
      "href", expect.stringContaining("mailto:polarisbuiltinc@gmail.com"),
    );
    expect(screen.queryByTestId("harness-download-button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
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
});
