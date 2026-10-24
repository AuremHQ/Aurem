/**
 * Admin.navIconMapping.test.jsx — 2026-10-24
 *
 * Founder report: "/admin page won't open, nor any admin sidebar item"
 * — the WHOLE Admin page crashed (not just one tab) with React's
 * "Element type is invalid" because `adminNav.js` registered a
 * `pricing_panel` nav item that had no matching key in Admin.jsx's
 * `NAV_ICONS` map. `buildGroupedAdminNav` silently resolved
 * `Icon: undefined` for that one row, and rendering a <button
 * Icon={undefined}/> for ANY row in the sidebar crashes React's
 * render of the WHOLE <aside>, not just that row — same failure
 * class as the `financials` icon gap fixed earlier (97e6aa3e).
 *
 * This locks in the invariant going forward: every id in
 * ADMIN_NAV must have a matching key in Admin.jsx's NAV_ICONS,
 * so a future nav addition that forgets the icon fails a fast,
 * obvious unit test instead of crashing the live admin page.
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";
import { ADMIN_NAV } from "../../lib/adminNav.js";

describe("Admin sidebar — every nav id has a matching icon", () => {
  it("NAV_ICONS in Admin.jsx covers every ADMIN_NAV id", () => {
    const adminSrc = fs.readFileSync(
      path.join(__dirname, "../Admin.jsx"), "utf8"
    );
    const block = adminSrc.match(/const NAV_ICONS = \{([\s\S]*?)\n\};/);
    expect(block, "NAV_ICONS object not found in Admin.jsx").toBeTruthy();
    const iconKeys = new Set(
      [...block[1].matchAll(/^\s*([a-z_]+):/gm)].map((m) => m[1])
    );
    const missing = ADMIN_NAV.map((item) => item.id).filter(
      (id) => !iconKeys.has(id)
    );
    expect(missing, `ADMIN_NAV ids with no NAV_ICONS entry (would crash the sidebar): ${missing.join(", ")}`).toEqual([]);
  });
});
