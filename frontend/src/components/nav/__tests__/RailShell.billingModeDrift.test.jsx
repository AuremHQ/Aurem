/**
 * RailShell.billingModeDrift.test.jsx
 *
 * ITEM 5(d) (independent review) — confirms the billing-mode drift
 * check is wired into the authenticated shell on mount. Source-level
 * contract test, matching this codebase's own established pattern
 * for RailShell (a full render needs a large mocked prop/context
 * surface — see this directory's sibling test files).
 */
import fs from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

const RAIL_SHELL_SRC = fs.readFileSync(
  path.resolve(__dirname, "../RailShell.jsx"),
  "utf-8",
);

describe("RailShell — billing-mode drift check wired into the authenticated shell", () => {
  it("imports checkBillingModeDriftOnce and buildTimeBillingMode", () => {
    expect(RAIL_SHELL_SRC).toMatch(/import \{ checkBillingModeDriftOnce \} from ["']..\/..\/lib\/billingModeCheck["']/);
    expect(RAIL_SHELL_SRC).toMatch(/import \{ buildTimeBillingMode \} from ["']..\/..\/lib\/billingCopy["']/);
  });

  it("calls checkBillingModeDriftOnce exactly once on mount (empty dependency array)", () => {
    const match = RAIL_SHELL_SRC.match(
      /useEffect\(\(\) => \{\s*checkBillingModeDriftOnce\(\{ buildMode: buildTimeBillingMode\(\) \}\);\s*\}, \[\]\);/,
    );
    expect(match, "mount-only useEffect calling checkBillingModeDriftOnce not found").toBeTruthy();
  });
});
