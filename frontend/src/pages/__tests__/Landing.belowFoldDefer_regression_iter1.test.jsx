/**
 * Regression test for the Item-B Lighthouse TBT fix on Landing.jsx
 * (2026 quality-gate follow-up).
 *
 * Root cause being guarded against: the below-the-fold sections used
 * to mount in the SAME synchronous commit as the hero, so ~1,800
 * elements were rendered/laid-out before the page could ever be
 * "interactive", inflating Total Blocking Time. The fix defers that
 * whole block (`showBelowFold`) to the next idle slot / tick so the
 * hero paints first, then the rest mounts in a second, later commit.
 *
 * This test locks in BOTH halves of that contract so neither can
 * silently regress:
 *   1. On first render, the below-fold block (pricing, FAQ, footer,
 *      etc.) is NOT in the DOM yet — only the hero is.
 *   2. Shortly after (idle callback / fallback timer fires), the
 *      below-fold block mounts and becomes visible.
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({ data: {} })) },
}));
vi.mock("../../components/PricingCards", () => ({ default: () => <div data-testid="pricing-cards-stub" /> }));
vi.mock("../../components/LazyVideo", () => ({ default: () => <div data-testid="lazy-video-stub" /> }));
vi.mock("../../components/marketing/MarketingNav", () => ({ default: () => <div data-testid="marketing-nav-stub" /> }));
vi.mock("../../components/demo/WalkthroughPlayer", () => ({ default: () => <div data-testid="walkthrough-player-stub" /> }));
vi.mock("../../components/demo/demoSteps", () => ({ FULL_STEPS: [] }));

import Landing from "../Landing";

describe("Landing — below-the-fold defer (TBT regression)", () => {
  beforeEach(() => {
    // jsdom has no requestIdleCallback — this exercises the
    // setTimeout(mount, 0) fallback path used in CI/test/older browsers.
    delete window.requestIdleCallback;
    delete window.cancelIdleCallback;
  });

  it("does not mount the below-fold block on first render", () => {
    render(
      <MemoryRouter>
        <Landing />
      </MemoryRouter>
    );
    // Hero is present immediately.
    expect(screen.getByTestId("marketing-nav-stub")).toBeInTheDocument();
    // Pricing (below-fold) is not mounted yet.
    expect(screen.queryByTestId("pricing-cards-stub")).not.toBeInTheDocument();
  });

  it("mounts the below-fold block shortly after (idle/fallback fires)", async () => {
    render(
      <MemoryRouter>
        <Landing />
      </MemoryRouter>
    );
    await waitFor(() =>
      expect(screen.getByTestId("pricing-cards-stub")).toBeInTheDocument()
    );
  });
});
