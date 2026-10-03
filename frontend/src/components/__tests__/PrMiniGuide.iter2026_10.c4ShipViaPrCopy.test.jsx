/**
 * 2026-10 — C4 fix regression test.
 *
 * Bug: PrMiniGuide (the pre-ship "What happens next" card shown in
 * ShipDialog / ShipConfirmModal) unconditionally said "We'll open a
 * pull request on your GitHub" — true only when the `ship_via_pr`
 * feature flag is on. In production the flag is OFF (verified via
 * this preview pod's Mongo `feature_flags` collection, whose own
 * `description` field reads "prod stays OFF") — every real ship
 * commits straight to the base branch, so every production user saw
 * a false promise.
 *
 * Fix: PrMiniGuide now reads `isShipViaPrEnabled()` from
 * lib/pageFlags.js — populated from /auth/me's new
 * `ship_via_pr_enabled` field, itself the same is_enabled() call
 * that gates the real ship path (backend/routers/loop.py). The copy
 * can't drift from the real behavior again.
 */
import { describe, it, expect, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import PrMiniGuide from "../PrMiniGuide";
import { setPageFlags } from "../../lib/pageFlags";

describe("PrMiniGuide — copy must match the real ship_via_pr flag state", () => {
  beforeEach(() => {
    try { localStorage.removeItem("aurem_pr_mini_guide_seen_v1"); } catch { /* ignore */ }
  });

  it("flag OFF (production default): says 'commits directly', never 'pull request'", () => {
    setPageFlags({ ship_via_pr_enabled: false });
    render(<PrMiniGuide />);
    expect(screen.getByTestId("pr-mini-guide-direct-copy")).toBeTruthy();
    expect(screen.queryByText(/pull request/i)).toBeNull();
    expect(screen.getByText(/commits directly to your main branch/i)).toBeTruthy();
    expect(screen.getByText(/roll back/i)).toBeTruthy();
  });

  it("flag ON: says 'pull request', not the direct-commit copy", () => {
    setPageFlags({ ship_via_pr_enabled: true });
    render(<PrMiniGuide />);
    expect(screen.getByTestId("pr-mini-guide-pr-copy")).toBeTruthy();
    expect(screen.queryByText(/commits directly to your main branch/i)).toBeNull();
    expect(screen.getByText(/open a pull request/i)).toBeTruthy();
  });

  it("no flag data yet (pageFlags still at its false default) reads as OFF, not a guess", () => {
    // Simulates the window before /auth/me has resolved on page load —
    // must fail safe to the truthful "direct commit" copy, never the
    // PR promise, since PR-on is the less common, opt-in case.
    setPageFlags(undefined);
    render(<PrMiniGuide />);
    expect(screen.getByTestId("pr-mini-guide-direct-copy")).toBeTruthy();
  });
});
