/**
 * auremReadmeNotPublic.test.js
 *
 * ITEM 7(a) (independent review) — AUREM_README.md was an internal
 * engineering doc (architecture maps, runbook, tech stack) served
 * publicly from frontend/public/ (and therefore copied verbatim into
 * the built dist/ by Vite). Moved to docs/AUREM_README.md via git mv
 * (not deleted). No src/ code or public/ file referenced the old
 * public path (grepped before moving), so nothing needed updating.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

describe("AUREM_README.md is no longer served publicly", () => {
  it("does not exist under frontend/public/", () => {
    const oldPath = path.join(REPO_ROOT, "frontend", "public", "AUREM_README.md");
    expect(fs.existsSync(oldPath)).toBe(false);
  });

  it("exists under docs/ (moved, not deleted)", () => {
    const newPath = path.join(REPO_ROOT, "docs", "AUREM_README.md");
    expect(fs.existsSync(newPath)).toBe(true);
  });

  it("no frontend source file references the old public path", () => {
    const srcDir = path.join(REPO_ROOT, "frontend", "src");
    const hits = [];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__tests__") walk(full);
        } else if (/\.(jsx?|tsx?|html)$/.test(entry.name)) {
          const text = fs.readFileSync(full, "utf-8");
          if (text.includes("AUREM_README")) hits.push(full);
        }
      }
    };
    walk(srcDir);
    expect(hits).toEqual([]);
  });
});
