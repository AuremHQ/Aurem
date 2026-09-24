/**
 * lib/siteConfig.js — Bug 7 fix (2026-09-17): the page title flipped
 * between "ORA by Aurem — developers choice" (set by Landing.jsx,
 * never restored on unmount) and "ORA by Aurem — The AI Engineer
 * That Actually Commits" (the static default in index.html), plus a
 * THIRD variant in usePageMeta.js's own DEFAULT_TITLE constant — three
 * independently hand-typed brand strings that drifted out of sync.
 *
 * One source of truth: every route imports `SITE`/`pageTitle()` from
 * here instead of re-typing the name/tagline. `index.html`'s static
 * `<title>` is the one place that can't import a JS module at parse
 * time — keep it byte-for-byte equal to `pageTitle()`'s no-arg output
 * (asserted in a test) so it can't silently drift again either.
 */
export const SITE = {
  name: "ORA by Aurem",
  tagline: "The AI Engineer That Actually Commits",
};

/**
 * `pageTitle()`                -> "ORA by Aurem — The AI Engineer That Actually Commits | $9/mo"
 *                                  (byte-for-byte the same as index.html's static <title> —
 *                                  see test_siteConfig_matches_index_html_title.test.js)
 * `pageTitle("Developers")`    -> "Developers — ORA by Aurem"
 */
export function pageTitle(section) {
  return section
    ? `${section} — ${SITE.name}`
    : `${SITE.name} — ${SITE.tagline} | $9/mo`;
}
