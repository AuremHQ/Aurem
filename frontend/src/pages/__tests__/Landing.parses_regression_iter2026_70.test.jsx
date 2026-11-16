// @vitest-environment node
/**
 * Regression (iter 2026-70): Landing.jsx on main contained pasted model
 * output ("// ... (rest of the file remains exactly the same ...)" and
 * loose JSX fragments) plus a CSS block replaced by a comment, so
 * `vite build` failed with `Expected ";" but found "className"` at
 * Landing.jsx:570 and Lighthouse CI went red.
 *
 * This compiles the real file with the same esbuild transform Vite
 * uses, so a corrupt Landing.jsx fails here instead of at deploy time.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { transformWithEsbuild } from "vite";

const FILE = path.resolve(__dirname, "..", "Landing.jsx");
const SRC = fs.readFileSync(FILE, "utf-8");

function listSources(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (["node_modules", "dist", "build", ".git"].includes(e.name)) continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) listSources(p, out);
    else if (/\.(jsx?|mjs|cjs)$/.test(e.name)) out.push(p);
  }
  return out;
}

describe("every frontend source file compiles", () => {
  it("no file under src/ fails the Vite esbuild transform", async () => {
    const root = path.resolve(__dirname, "..", "..");
    const failures = [];
    for (const f of listSources(root)) {
      try {
        await transformWithEsbuild(fs.readFileSync(f, "utf-8"), f, { loader: "jsx" });
      } catch (e) {
        failures.push(`${path.relative(root, f)}: ${String(e.message).split("\n")[0].slice(0, 100)}`);
      }
    }
    expect(failures, failures.join("\n")).toEqual([]);
  }, 60000);

  it("billingCopy.js still exports what the pages import", async () => {
    const mod = fs.readFileSync(path.resolve(__dirname, "..", "..", "lib", "billingCopy.js"), "utf-8");
    for (const name of ["TOKEN_WORDING_ACTIVE", "heroFreeOfferCta", "heroTokenMetersClaim",
                        "landingPricingSectionSub", "heroStatTokenBilling", "comparisonSectionSub",
                        "comparisonFlatPricingRowLabel"]) {
      expect(new RegExp(`export (const|function) ${name}\\b`).test(mod), name).toBe(true);
    }
    expect((mod.match(/export function heroTokenMetersClaim\b/g) || []).length).toBe(1);
  });
});

describe("Landing.jsx is compilable and not truncated", () => {
  it("compiles with the Vite esbuild transform", async () => {
    const out = await transformWithEsbuild(SRC, FILE, { loader: "jsx" });
    expect(out.code.length).toBeGreaterThan(1000);
  });

  it("the transform rejects a corrupted copy (the checker works)", async () => {
    const corrupt = SRC.replace("export default", "// ...\n<div className=\"x\">\nexport default");
    await expect(transformWithEsbuild(corrupt, FILE, { loader: "jsx" })).rejects.toThrow();
  });

  it("keeps the stylesheet block that the corrupt version dropped", () => {
    expect(SRC).toContain(".ora-landing .footer-links");
    expect(SRC).toContain("@media (max-width: 720px)");
  });

  it("has no pasted model chatter", () => {
    for (const needle of ["rest of the file remains", "rest of the CSS remains"]) {
      expect(SRC.includes(needle), needle).toBe(false);
    }
  });
});
