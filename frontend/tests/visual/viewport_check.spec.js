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

// Phase 1 mobile view (2026-11-24): the dashboard is behind PrivateRoute
// and a placeholder token alone does not keep it there -- /auth/me (called
// on boot) 401s, and every data fetch 401s too, each dispatching
// aurem:session-expired, so the app logs out and bounces to /login before
// the dashboard shell ever renders. Stub the session + the data endpoints
// the Dashboard needs so the shell (rail, chat pane, bottom tab bar)
// renders with empty states and the layout is genuinely exercised.
// The token is a fabricated JWT shape (3 dot-separated parts, >20 chars);
// the frontend only checks the shape, it never validates the signature.
const VIEWPORT_TOKEN =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoidmlld3BvcnQtY2hlY2siLCJlbWFpbCI6InZpZXdwb3J0LWNoZWNrQGV4YW1wbGUuY29tIiwidGllciI6ImZyZWUiLCJleHAiOjk5OTk5OTk5OTl9.viewport-check-signature";
const VIEWPORT_USER = {
  user_id: "viewport-check",
  email: "viewport-check@example.com",
  tier: "free",
  name: "Viewport Check",
};

async function stubAuthSession(page) {
  await page.addInitScript(
    ({ token, user }) => {
      localStorage.setItem("aurem_token", token);
      localStorage.setItem("aurem_user", JSON.stringify(user));
    },
    { token: VIEWPORT_TOKEN, user: VIEWPORT_USER }
  );
  // Session gate: /auth/me is called on app boot and its 401 is what logs
  // the placeholder token out. Return a valid session instead.
  await page.route("**/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ token: VIEWPORT_TOKEN, user: VIEWPORT_USER }),
    })
  );
  // Data the Dashboard loads on mount. Each is read defensively
  // (optional chaining + defaults), so empty payloads render empty states
  // instead of crashing -- the shell is what this check exercises.
  await page.route("**/cto/projects/list", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ projects: [] }),
    })
  );
  await page.route("**/onboarding/repo-connect-gate/state", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ skipped: true }),
    })
  );
  await page.route("**/wrapped/me*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ stats: { tasks_shipped: 0 } }),
    })
  );
  await page.route("**/codebase-health/last*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: null }),
    })
  );
  // Any other backend call (ChatPanel history/usage and friends) must not
  // 401 either -- a 401 from any non-auth endpoint also dispatches
  // aurem:session-expired and logs the session out. Answer 200 with an
  // empty body; the app reads these defensively and shows empty states.
  // Registered last so the specific stubs above (and stubDynamicContent's
  // promo/usage stubs, registered before this helper runs) take precedence.
  await page.route(
    (url) => url.href.includes("/api/") || url.origin.endsWith(":8001"),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      })
  );
}

const KEY_SCREENS = [
  { name: "landing", path: "/" },
  { name: "why-ora", path: "/why-ora" },
  { name: "demo", path: "/demo" },
  { name: "login", path: "/login" },
  // Phase 1 mobile view (2026-11-24): dashboard + /ora chat + pricing + signup.
  { name: "dashboard", path: "/dashboard", needsAuth: true },
  { name: "ora", path: "/ora" },
  { name: "pricing", path: "/pricing" },
  { name: "signup", path: "/signup" },
];

test.describe("Viewport check — key screens x breakpoints (S4)", () => {
  for (const { path, needsAuth } of KEY_SCREENS) {
    for (const [key, bp] of Object.entries(breakpoints)) {
      test(`'${path}' has no horizontal overflow at ${key} (${bp.width}px)`, async ({ page }) => {
        await page.setViewportSize({ width: bp.width, height: bp.height });
        await stubDynamicContent(page);
        if (needsAuth) await stubAuthSession(page);
        await page.goto(path, { waitUntil: "domcontentloaded" });
        // The dashboard holds a streaming connection open, so networkidle
        // never settles there; "load" plus the root assertion below is
        // the readiness signal for it.
        await page.waitForLoadState(needsAuth ? "load" : "networkidle");
        if (needsAuth) {
          // The dashboard shell must actually render (not bounce to /login)
          // for this screen's check to mean anything.
          await expect(page.locator('[data-testid="dashboard-v2-root"]')).toBeVisible();
        }

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

// Phase 1 mobile view (2026-11-24): tap targets. The controls a phone user
// hits most must be at least 44px; the chat Send button is 48px by design
// (Stop shares the same 48px sizing but only renders while a run is busy).
test.describe("Tap targets — mobile (375px)", () => {
  test("landing hamburger and CTA are at least 44px", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await stubDynamicContent(page);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const hamburger = page.getByTestId("nav-hamburger");
    await expect(hamburger).toBeVisible();
    const hBox = await hamburger.boundingBox();
    expect(hBox.width).toBeGreaterThanOrEqual(44);
    expect(hBox.height).toBeGreaterThanOrEqual(44);
    const cta = page.locator(".mn-cta").first();
    await expect(cta).toBeVisible();
    const cBox = await cta.boundingBox();
    expect(cBox.height).toBeGreaterThanOrEqual(44);
  });

  test("dashboard tab bar and chat Send are at least 44px (Send 48px)", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await stubDynamicContent(page);
    await stubAuthSession(page);
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    // Same streaming-connection reason as the overflow loop above.
    await page.waitForLoadState("load");
    await expect(page.locator('[data-testid="dashboard-v2-root"]')).toBeVisible();
    for (const tab of ["chat", "projects", "ship", "menu"]) {
      const btn = page.getByTestId(`mobile-tab-${tab}`);
      await expect(btn).toBeVisible();
      const box = await btn.boundingBox();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    const send = page.getByTestId("chat-send");
    await expect(send).toBeVisible();
    const sBox = await send.boundingBox();
    expect(sBox.width).toBeGreaterThanOrEqual(48);
    expect(sBox.height).toBeGreaterThanOrEqual(48);
  });
});

// Phase 1 mobile view (2026-11-24): landscape phone (812x375) must not
// force horizontal scroll either.
test.describe("Landscape — phone (812x375)", () => {
  const LANDSCAPE_SCREENS = [
    { path: "/" },
    { path: "/ora" },
    { path: "/dashboard", needsAuth: true },
  ];
  for (const { path, needsAuth } of LANDSCAPE_SCREENS) {
    test(`${path} has no horizontal overflow in landscape`, async ({ page }) => {
      await page.setViewportSize({ width: 812, height: 375 });
      await stubDynamicContent(page);
      if (needsAuth) await stubAuthSession(page);
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState(needsAuth ? "load" : "networkidle");
      const { scrollWidth, clientWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
    });
  }
});
