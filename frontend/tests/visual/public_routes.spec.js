/**
 * tests/visual/public_routes.spec.js — Iter 299 (Frontend QA Charter Layer 2)
 *
 * Visual regression for the 5 canonical UNAUTHENTICATED views.
 *
 * Auth-gated screens are deferred to Layer 2 Batch 2 (needs a
 * seeded founder session or a mocked auth cookie path).
 *
 * Each test:
 *   1. Navigates to the route.
 *   2. Freezes async work (network idle) so hero images finish
 *      loading before we snap.
 *   3. Neutralises time-based UI (any element with data-live-clock
 *      is hidden via added CSS).
 *   4. `expect(page).toHaveScreenshot()` compares against the
 *      baseline in `__screenshots__/`.
 *
 * On first run, baselines are generated automatically. On every
 * subsequent run, a >2% pixel-diff fails the test.
 *
 * Update baselines after a legitimate UI change:
 *     cd frontend && npx playwright test --update-snapshots
 * See docs/visual_regression.md for the full workflow.
 */
import { test, expect } from "@playwright/test";


// Iter 300 — root-cause fix for the flaky landing/login diffs: both
// were failing not from any CSS/design regression, but from a live
// data source that legitimately changes between baseline capture and
// test run: CookieConsentBanner (first-visit only) repositions the
// whole fold depending on whether it's showing. stubDynamicContent()
// neutralises it BEFORE first paint so every run renders the exact
// same fold, regardless of first-visit cookie status.
//
// 2026-09-26 — the homepage's `/promo/first50/status` poll this used
// to also stub is gone; the entire First-50 promo feature (hero chip,
// endpoint, everything) was removed from the codebase, so Landing.jsx
// no longer makes that call at all.
async function stubDynamicContent(page) {
  // Already-consented — CookieConsentBanner reads this on mount and
  // renders nothing when present (see cookieConsentStorage.js STORAGE_KEY).
  await page.addInitScript(() => {
    localStorage.setItem(
      "aurem_consent",
      JSON.stringify({
        v: 1,
        ts: "2026-01-01T00:00:00.000Z",
        choice: "essential",
        cats: { necessary: true, functional: true, analytics: false, marketing: false },
      })
    );
  });
  // Hide the live trust-badge tile — matches routers/usage.py's own
  // fallback shape so the page renders exactly as it does pre-data.
  await page.route("**/usage/public/stats", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ available: false }),
    })
  );
}

// Shared setup — freeze anything that would otherwise shift between
// runs (animations, live clocks, video autoplay).
async function freezePage(page) {
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation-duration: 0s !important;
        animation-delay:    0s !important;
        transition-duration: 0s !important;
        transition-delay:    0s !important;
      }
      [data-live-clock], [data-live-timestamp], video {
        visibility: hidden !important;
      }
    `,
  });
  // Fonts must be fully loaded before we snap — otherwise a fallback
  // font renders in frame 1 and the real font in frame 2, producing
  // a 100% flaky test.
  await page.evaluate(() => document.fonts?.ready);
  // A short settle for React hydration + any lazy-loaded hero images.
  await page.waitForLoadState("networkidle");
}


test.describe("Public routes — visual regression (iter 299)", () => {
  test("landing '/' renders the hero and fold consistently", async ({ page }) => {
    await stubDynamicContent(page);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await freezePage(page);
    await expect(page).toHaveScreenshot("landing.png", {
      fullPage: false,           // Just the fold — full-page snapshots
                                 // are too noisy across scroll heights.
    });
  });

  test("'/why-ora' marketing page long-content layout is stable", async ({ page }) => {
    await stubDynamicContent(page);
    await page.goto("/why-ora", { waitUntil: "domcontentloaded" });
    await freezePage(page);
    await expect(page).toHaveScreenshot("why-ora.png", {
      fullPage: false,
    });
  });

  test("'/demo' page renders demo layout", async ({ page }) => {
    await stubDynamicContent(page);
    await page.goto("/demo", { waitUntil: "domcontentloaded" });
    await freezePage(page);
    await expect(page).toHaveScreenshot("demo.png", {
      fullPage: false,
    });
  });

  test("'/login' form UI is pixel-stable", async ({ page }) => {
    await stubDynamicContent(page);
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await freezePage(page);
    await expect(page).toHaveScreenshot("login.png", {
      fullPage: false,
    });
  });

  test("'/dev/loop-live-feed' component demo renders the extracted state-sync components", async ({ page }) => {
    // This route stitches together LoopStepBar + AgentStatusBar +
    // LoopLiveFeed — the three components that Batch 1 extracted.
    // A visual regression here catches state-sync bug classes that
    // slip past the RTL DOM-only assertions.
    await stubDynamicContent(page);
    await page.goto("/dev/loop-live-feed", { waitUntil: "domcontentloaded" });
    await freezePage(page);
    await expect(page).toHaveScreenshot("loop-live-feed-demo.png", {
      fullPage: false,
    });
  });
});
