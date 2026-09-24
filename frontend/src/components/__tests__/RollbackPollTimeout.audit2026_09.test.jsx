/**
 * 2026-09 admin/prod-audit fix — Rollback poll-timeout was hard-capped
 * at 30s in ShipConfirmModal.jsx, but a real production drill showed
 * the actual GitHub revert commit can take 5-6 minutes to land (see
 * routers/cto_projects/rollback.py's rollback_worker_started_at timing
 * field, added alongside this fix). At 30s the poll loop silently gave
 * up with no error surfaced, leaving the modal stuck on "shipped" —
 * looking exactly like rollback never happened.
 *
 * Source-text checks (matching this file's existing pattern, e.g.
 * ShipConfirmModal.p1b_honest_label.test.jsx) since jsdom fake-timer
 * + async-poll-loop interaction is brittle; asserting the actual
 * budget/message text in source is the reliable regression signal.
 */
import { readFileSync } from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const SRC = readFileSync(
  path.resolve(__dirname, "../ShipConfirmModal.jsx"),
  "utf-8"
);

describe("ShipConfirmModal — rollback poll timeout hardening (2026-09)", () => {
  it("no longer hard-caps the rollback poll at 30 seconds", () => {
    expect(SRC).not.toMatch(/Date\.now\(\)\s*\+\s*30_000/);
  });

  it("extends the rollback poll budget to 10 minutes", () => {
    expect(SRC).toContain("10 * 60_000");
  });

  it("surfaces a visible message instead of silently giving up on timeout", () => {
    expect(SRC).toMatch(/still running in the background/);
  });

  it("still checks rollback_status === 'failed' as a terminal state", () => {
    expect(SRC).toContain('t?.rollback_status === "failed"');
  });
});

const MB_SRC = readFileSync(
  path.resolve(__dirname, "../MessageBubble.jsx"),
  "utf-8"
);

describe("MessageBubble — rollback fire-and-forget fix (2026-09)", () => {
  it("no longer shows the bare unconditional 'Rollback queued' with no follow-up", () => {
    expect(MB_SRC).not.toMatch(/message:\s*"Rollback queued",\s*kind:\s*"info"\s*\}\);\s*\}\s*catch/);
  });

  it("kicks off a rollback status poll after queuing", () => {
    expect(MB_SRC).toContain("setRollbackPollTick((n) => n + 1)");
  });

  it("has a bounded rollback poll effect keyed on rollbackPollTick", () => {
    expect(MB_SRC).toContain("if (!rollbackPollTick) return;");
    expect(MB_SRC).toContain("MAX_ATTEMPTS = 200");
  });

  it("shows a real success toast when rollback_status flips to done", () => {
    expect(MB_SRC).toContain("Rollback complete — commit reverted on GitHub");
  });
});
