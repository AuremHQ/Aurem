// src/__tests__/bootWatchdog.test.js - bounded boot loading state.
// The inline watchdog in frontend/index.html must, after its single
// stale-bundle reload, show a human-readable error with a boot request
// ID, a safe Retry, Sign out and Support - never leave the user stuck
// on the loading splash with no escape.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const indexHtml = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "..", "index.html"),
  "utf8"
);

describe("boot watchdog (index.html)", () => {
  it("shows an error panel with request ID, Retry, Sign out and Support", () => {
    expect(indexHtml).toContain("aurem-boot-error");
    expect(indexHtml).toContain("aurem-boot-request-id");
    expect(indexHtml).toContain("aurem-boot-retry");
    expect(indexHtml).toContain("aurem-boot-signout");
    expect(indexHtml).toContain("aurem-boot-support");
  });

  it("keeps the single forced reload guard", () => {
    expect(indexHtml).toContain("aurem_stale_reload_attempted");
  });

  it("generates a boot request ID and reports the mount timeout", () => {
    expect(indexHtml).toContain("aurem_boot_request_id");
    expect(indexHtml).toContain("boot-mount-timeout");
  });
});
