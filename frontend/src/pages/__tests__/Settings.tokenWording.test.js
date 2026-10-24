/**
 * Settings.tokenWording.test.js — Master Prompt v2 PART 5.1/5.2.
 * Source-inspection test (same convention as
 * Settings.track_switcher_hidden.test.js, used in this file because
 * Settings.jsx has too many live dependencies to cheaply full-render
 * in a unit test) locking: the "tasks this month" tile and Profile
 * row are gated behind TOKEN_WORDING_ACTIVE, not unconditional, and
 * the balance-card heading swaps wording too.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SETTINGS = path.resolve(__dirname, "..", "Settings.jsx");

describe("Settings.jsx — Master Prompt v2 PART 5.1 token wording gate", () => {
  const src = fs.readFileSync(SETTINGS, "utf-8");

  it("imports TOKEN_WORDING_ACTIVE from the shared billingCopy module", () => {
    expect(src).toMatch(/import \{ TOKEN_WORDING_ACTIVE \} from "\.\.\/lib\/billingCopy"/);
  });

  it("the balance-card heading swaps wording based on the flag", () => {
    expect(src).toMatch(/TOKEN_WORDING_ACTIVE \? "Token balance & plan usage" : "Tokens, tasks & plan usage"/);
  });

  it("the 'tasks this month' tile is gated behind !TOKEN_WORDING_ACTIVE, not unconditional", () => {
    expect(src).toMatch(/\{!TOKEN_WORDING_ACTIVE && \(\s*<div data-testid="key-tasks-this-month"/);
  });

  it("the Profile section's 'tasks this month' rows are gated behind !TOKEN_WORDING_ACTIVE", () => {
    expect(src).toMatch(/!TOKEN_WORDING_ACTIVE && usage && usage\.monthly_task_cap != null/);
    expect(src).toMatch(/!TOKEN_WORDING_ACTIVE && usage && usage\.monthly_task_cap == null/);
  });

  it("the 'tokens remaining'/'tokens used' tiles are untouched (always shown, both modes)", () => {
    expect(src).toMatch(/data-testid="key-balance-tokens"/);
    expect(src).toMatch(/data-testid="key-balance-used"/);
  });
});
