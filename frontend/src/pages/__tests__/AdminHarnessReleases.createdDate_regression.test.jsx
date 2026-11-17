/**
 * AdminHarnessReleases.createdDate_regression.test.jsx
 *
 * 2026-11-16 — the CREATED column on /admin/harness-releases showed
 * "20752d ago" for a real row. Root cause: created_at/published_at
 * are SECONDS since epoch (Python time.time()), but `new Date(iso)`
 * treats a raw number as MILLISECONDS — landing the computed date
 * ~57 years off. Fix: multiply by 1000, and show an absolute date
 * instead of a relative one.
 *
 * Same source-grep convention as Landing.harnessPromo.test.jsx —
 * no RTL render harness exists yet for this page, and a full mock
 * (api, toast, getToken) is out of proportion to a one-function fix.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SRC = fs.readFileSync(path.resolve(__dirname, "..", "AdminHarnessReleases.jsx"), "utf-8");

describe("AdminHarnessReleases.jsx — ago() CREATED-date fix", () => {
  it("multiplies the seconds-since-epoch timestamp by 1000 before constructing a Date", () => {
    expect(SRC).toMatch(/new Date\(seconds \* 1000\)/);
  });

  it("no longer returns a relative 'Xd/Xh/Xm/Xs ago' string", () => {
    const fnBody = SRC.match(/function ago\(seconds\) \{[\s\S]*?\n\}/)?.[0] ?? "";
    expect(fnBody, "ago() function not found").toBeTruthy();
    expect(fnBody).not.toMatch(/ ago`/);
  });

  it("formats an absolute date with day, short month name, year, and HH:MM", () => {
    const fnBody = SRC.match(/function ago\(seconds\) \{[\s\S]*?\n\}/)?.[0] ?? "";
    expect(fnBody).toMatch(/getDate\(\)/);
    expect(fnBody).toMatch(/getFullYear\(\)/);
    expect(fnBody).toMatch(/getHours\(\)/);
    expect(fnBody).toMatch(/getMinutes\(\)/);
  });

  it("falls back to an em dash for a missing or invalid timestamp, never a crash", () => {
    const fnBody = SRC.match(/function ago\(seconds\) \{[\s\S]*?\n\}/)?.[0] ?? "";
    expect(fnBody).toMatch(/if \(!seconds\) return "—";/);
    expect(fnBody).toMatch(/isNaN\(d\.getTime\(\)\)/);
  });
});
