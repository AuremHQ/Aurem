// Pure detection logic for check-stable-first-paint.
// Kept separate from Node.js filesystem/CLI code so Vitest can import it.

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
