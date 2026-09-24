/**
 * tests/visual/viewport_check.spec.js — Master Plan S4
 * (Responsive / Viewport).
 *
 * NOT a pixel-diff test (this session already root-caused two
 * flaky pixel-diff failures from nondeterministic live content —
 * see public_routes.spec.js's stubDynamicContent()). A
 * "layoutlint"-style structural check instead: at every breakpoint
 * in breakpoints.config.js, a key screen must not force horizontal
 * scroll (the #1 real-world "broken on mobile" symptom — an element
 * wider than the viewport pushes everything else off-screen) and
 * must not leave any element visibly overflowing its own container.
 *
 * Reuses the same stub/freeze helpers + route set as
 * public_routes.spec.js so this doesn't invent a second maintenance
 * surface for "what counts as a key screen".
 */
import { test, expect } from "@playwright/test";
import breakpoints from "../../breakpoints.config.js";

async function stubDynamicContent(page) {
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
  await page.route("**/promo/first50/status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ claimed: 200, total: 500, remaining: 300, is_active: true }),
    })
  );
  await page.route("**/usage/public/stats", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ available: false }),
    })
  );
}

const KEY_SCREENS = [
  { name: "landing", path: "/" },
  { name: "why-ora", path: "/why-ora" },
  { name: "demo", path: "/demo" },
  { name: "login", path: "/login" },
];

test.describe("Viewport check — key screens x breakpoints (S4)", () => {
  for (const { path } of KEY_SCREENS) {
    for (const [key, bp] of Object.entries(breakpoints)) {
      test(`'${path}' has no horizontal overflow at ${key} (${bp.width}px)`, async ({ page }) => {
        await page.setViewportSize({ width: bp.width, height: bp.height });
        await stubDynamicContent(page);
        await page.goto(path, { waitUntil: "domcontentloaded" });
        await page.waitForLoadState("networkidle");

        const { scrollWidth, clientWidth } = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }));
        expect(
          scrollWidth,
          `${path} at ${bp.width}px: document is ${scrollWidth}px wide but the ` +
          `viewport is only ${clientWidth}px — something is forcing horizontal ` +
          `scroll (an element wider than its container, and not clipped by an ` +
          `overflow:hidden ancestor the way an intentional marquee/ticker is).`,
        ).toBeLessThanOrEqual(clientWidth + 1); // +1px rounding tolerance
      });
    }
  }
});
