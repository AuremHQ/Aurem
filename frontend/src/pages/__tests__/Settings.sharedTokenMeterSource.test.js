/**
 * Settings.sharedTokenMeterSource.test.js — ITEM 8 (independent
 * review).
 *
 * Source-inspection test (same convention as
 * Settings.tokenWording.test.js and Settings.track_switcher_hidden.test.js,
 * used here because Settings.jsx has too many live dependencies to
 * cheaply full-render in a unit test) locking: Settings.jsx now reads
 * the balance tiles from the SAME hook as components/TokenMeter.jsx
 * (hooks/useTokenMeter.js, GET /usage/me/meter) instead of the
 * legacy, non-gating /usage/me "remaining" number it used before —
 * falling back to that legacy number only when the real meter isn't
 * visible yet (no paid plan / TOKEN_BILLING_MODE still "off").
 *
 * The actual "both show the same number for the same mocked API
 * response" behavioral proof lives in
 * hooks/__tests__/useTokenMeter.test.jsx (mounts the real TokenMeter
 * component alongside a hook consumer against one shared mock) —
 * Settings.jsx uses the identical hook, so there is no second data
 * path left to diverge.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "Settings.jsx"), "utf-8");

describe("Settings.jsx — shares TokenMeter's data source (ITEM 8)", () => {
  it("imports useTokenMeter from the same hook TokenMeter.jsx uses", () => {
    expect(SRC).toMatch(/import \{ useTokenMeter \} from "\.\.\/hooks\/useTokenMeter"/);
  });

  it("calls the hook once, destructuring meter", () => {
    expect(SRC).toMatch(/const \{ meter: tokenMeter \} = useTokenMeter\(\)/);
  });

  it("'tokens remaining' prefers the real meter balance when visible", () => {
    expect(SRC).toMatch(/tokenMeter\?\.visible\s*\n\s*\? Number\(tokenMeter\.balance\)\.toLocaleString\(\)/);
  });

  it("'tokens used' derives from the meter's base-minus-balance when visible", () => {
    expect(SRC).toMatch(/tokenMeter\?\.visible && tokenMeter\.base != null && tokenMeter\.balance != null/);
    expect(SRC).toMatch(/Number\(tokenMeter\.base - tokenMeter\.balance\)\.toLocaleString\(\)/);
  });

  it("still falls back to the legacy /usage/me numbers when the meter is not visible (no regression at mode=off)", () => {
    expect(SRC).toMatch(/usage\?\.remaining != null\s*\n\s*\? Number\(usage\.remaining\)\.toLocaleString\(\)/);
    expect(SRC).toMatch(/usage\?\.used != null\s*\n\s*\? Number\(usage\.used\)\.toLocaleString\(\)/);
  });
});
