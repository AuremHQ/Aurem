#!/usr/bin/env node
/**
 * scripts/check-stable-first-paint.mjs — Master Plan S3
 * ("stable-first-paint" core rule + CI lint gate).
 *
 * WHY: this app is a Vite CSR SPA (`ReactDOM.createRoot(...).render`,
 * not `hydrateRoot`) — but `scripts/seo-prerender.mjs` writes REAL
 * static HTML into `#root` for several public routes (/, /about,
 * /contact, /privacy, /developers, /vs/*, /compare) so crawlers see
 * real content. `createRoot().render()` doesn't reconcile against
 * that existing markup — it WIPES it and paints fresh. A prior fix
 * (seo-prerender.mjs, "2026-09-04 · flash-of-unstyled-content fix")
 * already had to patch around exactly this: real visitors briefly
 * saw the raw crawler snapshot before the styled SPA replaced it.
 *
 * The other half of that same failure class, NOT yet guarded: a
 * component whose FIRST render reads a browser-only value
 * (window/document/localStorage/sessionStorage/navigator/matchMedia)
 * or a non-deterministic one (Math.random/Date.now/new Date)
 * directly as a `useState` initializer. That value can't be known
 * at prerender time and isn't stable between the crawler snapshot
 * and the live client's first paint — same visible-flash risk,
 * plus a real layout-shift risk even on routes with no prerender at
 * all (e.g. reading matchMedia for dark/light mode picks a default,
 * then "pops" once the real value is available).
 *
 * RULE: a `useState(...)` initializer must not directly reference
 * a client-only/non-deterministic value. Read it in a `useEffect`
 * (post-mount, after first paint) and update state there instead —
 * first paint stays deterministic, the real value applies right
 * after without a visible flash tied to prerender/reconciliation.
 *
 * Scope: frontend/src/**\/*.{js,jsx} (all render-path files — this
 * bites on any CSR route, not just the 7 prerendered ones).
 *
 * Usage: node scripts/check-stable-first-paint.mjs
 * Exit 0 = clean. Exit 1 = violations found (prints file:line + why).
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "src");

const RISKY_PATTERN = new RegExp(
  [
    "\\bwindow\\.", "\\bdocument\\.",
    "\\blocalStorage\\.", "\\bsessionStorage\\.",
    "\\bnavigator\\.", "\\bmatchMedia\\(",
    "Math\\.random\\(\\)", "Date\\.now\\(\\)", "new Date\\(",
  ].join("|"),
);

const SAFE_GUARD_PATTERN = /typeof\s+window\s*!==?\s*["']undefined["']/;

/** Extract the `(...)` argument text of every `useState(` call in
 * `src`, handling one level of nested parens (covers the realistic
 * cases: function calls, ternaries, object/array literals). Multi-
 * line initializers are supported by scanning char-by-char instead
 * of a single-line regex. */
function findUseStateArgs(src) {
  const calls = [];
  const re = /\buseState\s*\(/g;
  let m;
  while ((m = re.exec(src))) {
    let depth = 1;
    let i = m.index + m[0].length;
    const start = i;
    while (i < src.length && depth > 0) {
      if (src[i] === "(") depth += 1;
      else if (src[i] === ")") depth -= 1;
      i += 1;
    }
    const argText = src.slice(start, i - 1);
    const line = src.slice(0, m.index).split("\n").length;
    calls.push({ argText, line });
  }
  return calls;
}

export function checkFile(path, src) {
  const violations = [];
  for (const { argText, line } of findUseStateArgs(src)) {
    if (SAFE_GUARD_PATTERN.test(argText)) continue; // explicitly SSR/CSR-safe guarded
    if (RISKY_PATTERN.test(argText)) {
      const matched = argText.match(RISKY_PATTERN)[0];
      violations.push({
        path, line,
        message:
          `useState(...) initializer reads a client-only/non-deterministic ` +
          `value ("${matched}") directly — first paint can't be stable. ` +
          `Default it deterministically, then set the real value inside ` +
          `a useEffect (after mount).`,
      });
    }
  }
  return violations;
}

/** Recursively collect .js/.jsx files under `dir`, skipping test
 * files/dirs — no new dependency needed for a plain directory walk. */
function walk(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "__tests__") continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, out);
    } else if (/\.(jsx?|tsx?)$/.test(entry.name) && !/\.test\.[jt]sx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

function scanFiles(files) {
  const all = [];
  for (const full of files) {
    const src = readFileSync(full, "utf8");
    all.push(...checkFile(full.startsWith(ROOT) ? full.slice(ROOT.length + 1) : full, src));
  }
  return all;
}

function report(all, scannedCount) {
  if (all.length === 0) {
    console.log(`[stable-first-paint] clean — ${scannedCount} file(s) scanned, 0 violations.`);
    return 0;
  }
  console.error(`[stable-first-paint] ${all.length} violation(s):\n`);
  for (const v of all) {
    console.error(`  ${v.path}:${v.line} — ${v.message}`);
  }
  return 1;
}

async function main() {
  const diffIdx = process.argv.indexOf("--diff");
  if (diffIdx !== -1) {
    // CI mode — same convention as backend/scripts/ci_check_new_bloat.py:
    // only NEWLY ADDED files (--diff-filter=A), never retroactive on the
    // pre-existing codebase. This app already has 26 pre-existing
    // localStorage/window/Date.now() useState initializers (found by
    // this very script on first run) — blocking on those would wall
    // off every future PR for unrelated reasons. Going forward, any
    // NEW file must be clean; existing ones are a separate cleanup.
    const { execSync } = await import("node:child_process");
    const [baseSha, headSha] = [process.argv[diffIdx + 1], process.argv[diffIdx + 2]];
    const added = execSync(
      `git diff --name-only --diff-filter=A ${baseSha} ${headSha} -- 'src/**/*.jsx' 'src/**/*.js'`,
      { cwd: ROOT, encoding: "utf8" },
    ).split("\n").filter(Boolean).filter((p) => !/\.test\.[jt]sx?$/.test(p));
    if (added.length === 0) {
      console.log("[stable-first-paint] no new src/ files in this diff — nothing to check.");
      return 0;
    }
    console.log(`New files in this diff (${added.length}):`);
    added.forEach((p) => console.log(`  ${p}`));
    const violations = scanFiles(added.map((p) => join(ROOT, p)));
    return report(violations, added.length);
  }
  const files = walk(SRC);
  const violations = scanFiles(files);
  return report(violations, files.length);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().then((code) => process.exit(code));
}
