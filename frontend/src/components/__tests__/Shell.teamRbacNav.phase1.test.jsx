/**
 * Shell.teamRbacNav.phase1.test.jsx — 2026-02 · Phase 1 (Team RBAC)
 *
 * "Every UI action a member can't perform must be hidden ... even
 * 'no access' resources are not visible in nav" — locks in that
 * Shell.jsx's sidebar NAV is resource-tagged and filtered by the
 * caller's real `/team/me` permissions before rendering (UX only;
 * the server-side gate is proven in
 * backend/tests/test_phase1_team_rbac_live_http.py).
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const SRC = fs.readFileSync(path.resolve(__dirname, "..", "Shell.jsx"), "utf-8");

describe("Shell.jsx — Phase 1 team-permission nav filtering", () => {
  it("tags each sensitive nav item with its RBAC resource key", () => {
    const expected = [
      ["dashboard", "projects", "deploy", "domain", "integrations",
       "billing", "reports", "automations", "team_management"],
    ][0];
    for (const resource of expected) {
      expect(SRC).toMatch(new RegExp(`resource:\\s*"${resource}"`));
    }
  });

  it("fetches /team/me to know what the current user can see", () => {
    expect(SRC).toMatch(/api\.get\(\s*["']\/team\/me["']\s*\)/);
  });

  it("filters NAV down to visibleNav using the resolved permissions, hiding 'none'", () => {
    expect(SRC).toMatch(/navPermissions\[n\.resource\]\s*!==\s*["']none["']/);
    expect(SRC).toMatch(/visibleNav\.map/);
  });

  it("fails open (shows the item) while permissions haven't loaded yet — UX only, never blocks nav", () => {
    expect(SRC).toMatch(/if \(!n\.resource \|\| !navPermissions\) return true;/);
  });
});
