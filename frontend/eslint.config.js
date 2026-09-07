// Minimal ESLint v9 flat config (2026-08-28 · Loop N item 4).
//
// This repo's authoritative linter is `oxlint` (zero-config, already
// runs clean — see CHANGELOG). This file exists ONLY so that if
// something on the platform side invokes bare `eslint` against this
// directory, it finds a valid flat config instead of failing with
// "ESLint couldn't find an eslint.config.js file" (an engine-level
// crash, not a real lint result). Kept intentionally permissive —
// it is not meant to replace oxlint or CI's lint gate.
const NOOP_RULE = { meta: { schema: [] }, create: () => ({}) };

module.exports = [
  {
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "**/build/**",
      "**/coverage/**",
      "**/playwright-report/**",
      "**/test-results/**",
      "backend/**",
      "_extract/**",
      "e2e-proof/**",
      "e2e_*/**",
      "**/.aurem_cache/**",
      "**/.emergent/**",
      "deployer-agent-docs/**",
      "deploy_scripts/**",
      "infra/**",
      "marketing/**",
      "docs/**",
      "gate4_fixture/**",
    ],
  },
  {
    // Both globs so the config also works when eslint is invoked from the
    // repo root (/app) via the root eslint.config.js re-export.
    files: [
      "src/**/*.{js,jsx}", "tests/**/*.{js,mjs}", "scripts/**/*.{js,mjs}",
      "frontend/src/**/*.{js,jsx}", "frontend/tests/**/*.{js,mjs}", "frontend/scripts/**/*.{js,mjs}",
    ],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    // NOTE: react/react-hooks plugins deliberately NOT registered
    // here. Installing eslint@9 + its plugins as LOCAL devDeps in
    // this repo conflicts with the existing `resolutions.brace-
    // expansion` pin in package.json (breaks @eslint/config-array's
    // bundled minimatch — "expand is not a function"). Rather than
    // touch that pin, the plugin namespaces below are STUBS (no-op
    // rules) so the `eslint-disable-next-line react-hooks/...` /
    // `react/...` comments already in src don't trip "Definition for
    // rule not found" when bare `eslint` runs. oxlint remains this
    // repo's actual zero-config linter (see CHANGELOG 2026-08-28).
    plugins: {
      "react-hooks": { rules: { "exhaustive-deps": NOOP_RULE, "set-state-in-effect": NOOP_RULE, "rules-of-hooks": NOOP_RULE } },
      react: { rules: { "no-danger": NOOP_RULE } },
    },
    linterOptions: { reportUnusedDisableDirectives: "off" },
    rules: {},
  },
];
