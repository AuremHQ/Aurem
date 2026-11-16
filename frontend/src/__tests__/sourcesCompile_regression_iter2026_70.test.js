// @vitest-environment node
/**
 * Regression (iter 2026-70): runs the Vite-grade esbuild transform over
 * every source file under src/ in a child process and fails if any file
 * does not compile (main had Landing.jsx and billingCopy.js corrupted).
 */
import { describe, it, expect } from "vitest";
import { spawnSync } from "node:child_process";
import path from "node:path";

const ROOT = path.join(__dirname, "..", "..");
const SCRIPT = path.join(ROOT, "scripts", "compile_sweep.cjs");

function sweep(dir) {
  const r = spawnSync(process.execPath, [SCRIPT, dir], { cwd: ROOT, encoding: "utf8" });
  expect(r.status, r.stderr).toBe(0);
  return JSON.parse(r.stdout.trim());
}

describe("every frontend source file compiles", () => {
  it("no file under src/ fails the esbuild transform", () => {
    const out = sweep(path.join(ROOT, "src"));
    expect(out.n).toBeGreaterThan(100);
    expect(out.bad, out.bad.join("\n")).toEqual([]);
  }, 60000);

  it("the sweep does detect a corrupt file", () => {
    const fs = require("node:fs");
    const os = require("node:os");
    const d = fs.mkdtempSync(path.join(os.tmpdir(), "cmp-"));
    fs.writeFileSync(path.join(d, "bad.jsx"), "export default () => <div className=\"x\"\n");
    const out = sweep(d);
    expect(out.bad.length).toBe(1);
  });
});
