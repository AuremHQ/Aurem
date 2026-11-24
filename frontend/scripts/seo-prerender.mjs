/**
 * seo-prerender.mjs — build-time SEO/GEO/AEO snapshots (Iter 358).
 *
 * WHY: the app is a client-rendered SPA — non-JS crawlers (and several
 * AEO/answer-engine bots) fetching /vs/* or /compare see the generic
 * landing <head> and an empty #root. This script runs AFTER `vite
 * build` and writes real static HTML for those routes into dist/:
 *
 *   dist/vs/<slug>/index.html   (5 competitor pages)
 *   dist/compare/index.html
 *
 * Each snapshot = that build's index.html with:
 *   - per-page <title>, meta description, canonical, og:* swapped
 *   - FAQPage/BreadcrumbList (or ItemList) JSON-LD injected
 *   - full semantic content rendered INSIDE <div id="root"> — React
 *     replaces it on mount, so browsers still get the live SPA while
 *     crawlers get real content (classic react-snap approach).
 *
 * Content comes from src/data/competitors.js — the SAME objects the
 * React pages render, so snapshots can never drift from the live UI.
 *
 * 2026-08-30 · AUREM public-site "agentic readiness" fixes (P0 #1):
 * the is-agentic.com scan found the homepage itself (and /about,
 * /contact, /privacy) serve ~60 chars of raw HTML to any crawler that
 * doesn't execute JS (AUREM's own research: 69% of AI crawlers can't
 * run JS). Same react-snap pattern extended to those four routes —
 * dist/index.html (the literal root file AND the SPA fallback shell
 * for every unmatched route) plus dist/about/, dist/contact/,
 * dist/privacy/. Content for about/contact comes from
 * src/data/companyInfo.mjs (same drift-proof pattern); privacy is the
 * real public/policies/privacy-policy.md, rendered through the same
 * `marked` parser PolicyPage.jsx uses client-side, so the static
 * snapshot and the live page always show identical text.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadEnv } from "vite";
import { marked } from "marked";
import {
  HERO, POINTS, BENCH, FEAT_ITEMS, FLOW_STEPS, REQUIREMENTS, INSTALL, FAQ_ITEMS,
  LEGAL_ARTICLES, FOOTER_COLS, FOOTER_TAGLINE, FOOTER_COPY, FOOTER_TRADEMARK,
} from "../src/data/harnessContent.mjs";

import {
  pricingHeroHeadline,
  pricingDetailLine,
  howMuchFaqAnswer,
  pricingFeatureCardDesc,
  comparisonSectionSub,
  comparisonFlatPricingRowLabel,
  landingPricingSectionSub,
  tierCapacityLine,
  freeOfferShortLine,
} from "../src/lib/billingCopy.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = join(ROOT, "dist");

// 2026-10-27 — homepage copy-only token-billing cutover (gated, same
// VITE_TOKEN_BILLING_MODE flag src/lib/billingCopy.js reads via
// `import.meta.env` in the browser bundle; this Node build script
// reads it from `process.env` instead since it runs outside Vite's
// module transform).
//
// 2026-11-09 — bugfix: this script runs as a SEPARATE `node` process
// after `vite build` in the same `&&`-chained `yarn build` command.
// `vite build` has its own internal env-file loader and correctly
// reads VITE_TOKEN_BILLING_MODE out of .env.production, but that is
// NOT an OS-level environment variable — it only exists as parsed
// file content inside Vite's own process. A second `node` command in
// the same shell chain does NOT inherit it via `process.env` unless
// something ALSO exported it at the shell level before `yarn build`
// ran. Call Vite's own `loadEnv` here so this script independently
// reads the exact same `.env`/`.env.production` files `vite build`
// just used, regardless of how the outer shell invoked the build.
// `process.env` still wins first (keeps the existing
// `VITE_TOKEN_BILLING_MODE=on node scripts/seo-prerender.mjs` manual
// test/debug workflow working unchanged).
const _loadedEnv = loadEnv("production", ROOT, "");
// Resolve ONE canonical value and propagate it onto process.env itself
// (not just a local const) so the dynamically-imported data modules
// below read the exact same resolved value this script computed from,
// regardless of which source (shell env vs .env.production) it came from.
process.env.VITE_TOKEN_BILLING_MODE =
  process.env.VITE_TOKEN_BILLING_MODE ?? _loadedEnv.VITE_TOKEN_BILLING_MODE ?? "off";
const TOKEN_MODE = process.env.VITE_TOKEN_BILLING_MODE === "on";

// 2026-11-09 — bugfix (same root cause as above, one level deeper):
// src/data/competitors.mjs and src/data/companyInfo.mjs compute their
// OWN `TOKEN_WORDING_ACTIVE` by reading `process.env.VITE_TOKEN_BILLING_MODE`
// directly (same dual-environment pattern, see those files). A static
// `import {...} from "../src/data/competitors.mjs"` at the top of this
// file is hoisted and evaluates BEFORE any of this file's own top-level
// code runs — including the `loadEnv` call above — so by the time that
// import ran, `process.env.VITE_TOKEN_BILLING_MODE` was still unset and
// those modules' own flags silently latched to `false`, no matter what
// TOKEN_MODE above correctly computed. Fix: actually SET the OS-level
// env var (above) before dynamically importing the data modules (not
// hoisted — runs exactly where it's written) so they see the exact
// same resolved value this script just computed.
const { COMPETITORS, COMPARE_HUB, LAST_VERIFIED } = await import("../src/data/competitors.mjs");
const { ABOUT, CONTACT } = await import("../src/data/companyInfo.mjs");
const { DEVELOPERS } = await import("../src/data/developerInfo.mjs");

// 2026-11-09 — "Aurem Loop" homepage promo section (draft, flag
// default OFF — do not flip without separate founder approval).
// Reads the SAME `harness_page` feature flag the authenticated
// /auth/me response exposes, via the new public (no-auth)
// GET /api/harness/page-status — single source of truth, so this
// static snapshot and the authenticated /harness page itself can
// never disagree about whether the feature is live. A network
// failure (backend not yet reachable at build time, timeout, etc.)
// fails closed to `false` — the same safe default as the flag simply
// not existing yet in Mongo.
async function resolveHarnessPageMode() {
  // 2026-11-21 — Aurem Deploy exception (DELIBERATE, keep): this is a
  // build-time Node script, not client code — it fetches the backend
  // while building, so it genuinely needs an absolute URL. It never
  // bakes the URL into the client bundle (only the fetched flag /
  // release data lands in the static HTML). Same for
  // resolveHarnessRelease() below.
  const backendUrl =
    process.env.VITE_API_URL ||
    _loadedEnv.VITE_API_URL ||
    process.env.REACT_APP_BACKEND_URL ||
    _loadedEnv.REACT_APP_BACKEND_URL;
  if (!backendUrl) return false;
  try {
    const resp = await fetch(`${backendUrl}/api/harness/page-status`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) return false;
    const body = await resp.json();
    return body?.harness_page_enabled === true;
  } catch (e) {
    console.warn(`[seo-prerender] harness/page-status fetch failed (${e.message}) — defaulting homepage promo section to hidden`);
    return false;
  }
}
const HARNESS_PAGE_MODE = await resolveHarnessPageMode();

// 2026-11-17 — the /harness page is now fully self-contained (the
// Midnight Gold rebuild: hero + inline sign-up/sign-in card + FAQ +
// Licensing & Policies + footer, all hardcoded in HarnessPage.jsx —
// no more fetch to the private AuremHQ/aurem-loop content repo). The
// ONLY thing that can still be real-vs-stale here is the release
// itself (version/sha256/size_bytes), via the SAME public, no-auth
// GET /api/harness/release-status the live page and the homepage
// promo section both read. A fetch failure fails closed to
// "unavailable" — same safe default as no release existing yet.
async function resolveHarnessRelease() {
  const backendUrl =
    process.env.VITE_API_URL ||
    _loadedEnv.VITE_API_URL ||
    process.env.REACT_APP_BACKEND_URL ||
    _loadedEnv.REACT_APP_BACKEND_URL;
  if (!backendUrl) return { available: false };
  try {
    const resp = await fetch(`${backendUrl}/api/harness/release-status`, {
      signal: AbortSignal.timeout(8000),
    });
    if (!resp.ok) return { available: false };
    return await resp.json();
  } catch (e) {
    console.warn(`[seo-prerender] harness/release-status fetch failed (${e.message}) — defaulting to unavailable`);
    return { available: false };
  }
}
const HARNESS_RELEASE = HARNESS_PAGE_MODE ? await resolveHarnessRelease() : { available: false };

// 2026-11-09 — bugfix: the comment above ("only the homepage snapshot
// is gated") was wrong once /vs/*, /compare and /about started
// carrying their own token-count claims (see src/data/competitors.mjs,
// src/data/companyInfo.mjs). Worse, EVERY non-homepage snapshot below
// inherits its <head>/<noscript> from `base` — the just-built
// dist/index.html — so the stale homepage phrases baked into that
// file's JSON-LD/noscript fallback were leaking into every /vs/*,
// /compare, /about, /contact, /privacy, /developers page too, even
// though each page's own <title>/description get correctly swapped
// per-page. Patch `base` itself, once, before any page is written
// from it. No-op (not an error) if a target phrase already changed.
function patchStaleTokenPhrases(html) {
  return html
    .replace(
      "The AI engineer that actually commits — $9/month, no IDE, no token billing.",
      "The AI engineer that actually commits — $9/month, no IDE, no per-action billing.",
    )
    .replace(
      "Flat monthly pricing starting at $9/month with 10 tasks free.",
      "Flat monthly pricing starting at $9/month with one free full scan, no card required.",
    )
    .replace(
      '"description": "10 tasks free per month. No credit card required."',
      '"description": "One free full repo scan. No credit card required."',
    )
    .replace(
      '"description": "$9/month flat — no token metering, no per-seat pricing, unlimited repositories. Limited to the first 500 users."',
      '"description": "$9/month flat for 900 tokens — no per-seat pricing, unlimited repositories, unused tokens carry forward. Limited to the first 500 users."',
    )
    .replace(
      '"Flat monthly pricing with no token metering",',
      '"Flat monthly pricing, no per-action billing — unused tokens carry forward",',
    )
    .replace(
      'there are 10 tasks free with no credit card. The $9 plan has no token metering and no per-seat pricing — the same monthly price whether ORA runs 5 tasks or 500.',
      'you get one free full repo scan with no credit card. The $9 plan grants 900 tokens/month, no per-seat pricing — unused tokens carry forward to the next month.',
    )
    .replace(
      "No IDE required, no token billing.",
      "No IDE required, no per-action billing.",
    )
    .replace(
      "10 free tasks on signup. No credit card required. 498 of 500",
      "One free full scan on signup. No credit card required. 498 of 500",
    );
}

const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// 2026-11-18 — AAO content audit: JSON-LD `text` fields must be plain
// text, not HTML — FAQ_ITEMS' `aHtml` carries markup (bold/links) for
// the visible page. Used only when building structured data below.
const stripHtml = (s) => String(s).replace(/<[^>]*>/g, "").trim();

function swapHead(html, { title, description, canonical }) {
  let out = html
    .replace(/<title>[\s\S]*?<\/title>/, () => `<title>${esc(title)}</title>`)
    .replace(/(<meta\s+name="description"\s+content=")[^"]*(")/,
      (_m, g1, g2) => `${g1}${esc(description)}${g2}`)
    .replace(/(<meta\s+property="og:title"\s+content=")[^"]*(")/,
      (_m, g1, g2) => `${g1}${esc(title)}${g2}`)
    .replace(/(<meta\s+property="og:description"\s+content=")[^"]*(")/,
      (_m, g1, g2) => `${g1}${esc(description)}${g2}`)
    .replace(/(<meta\s+property="og:url"\s+content=")[^"]*(")/,
      (_m, g1, g2) => `${g1}${esc(canonical)}${g2}`)
    .replace(/(<meta\s+name="twitter:title"\s+content=")[^"]*(")/,
      (_m, g1, g2) => `${g1}${esc(title)}${g2}`)
    .replace(/(<meta\s+name="twitter:description"\s+content=")[^"]*(")/,
      (_m, g1, g2) => `${g1}${esc(description)}${g2}`);
  if (/<link\s+rel="canonical"/.test(out)) {
    out = out.replace(/(<link\s+rel="canonical"\s+href=")[^"]*(")/,
      (_m, g1, g2) => `${g1}${esc(canonical)}${g2}`);
  } else {
    out = out.replace("</head>",
      `  <link rel="canonical" href="${esc(canonical)}" />\n</head>`);
  }
  return out;
}

function injectLd(html, ld) {
  return html.replace("</head>",
    `  <script type="application/ld+json">${JSON.stringify(ld)}</script>\n</head>`);
}

function injectRoot(html, inner) {
  // 2026-09-04 — flash-of-unstyled-content fix. `inner` is raw semantic
  // HTML (h1/p/ul, no classes) meant for non-JS crawlers. Real visitors
  // briefly see it verbatim before React's `createRoot().render()`
  // replaces #root's children — on a slow connection/bundle load that
  // showed up as an unstyled plain-text dump flashing before the
  // actual (styled) homepage, on every load/refresh. Scoping it with
  // the site's own dark theme (bg/text/accent match index.css) makes
  // that unavoidable pre-hydration window look intentional/branded
  // instead of broken. Crawlers that don't execute CSS still get the
  // exact same semantic markup/text — nothing removed, nothing hidden.
  // 2026-11-22 - the snapshot is crawler-only now. Founder report
  // 2026-11-21: the homepage showed the raw snapshot as a garbled
  // wall of text for ~14s on load (and indefinitely when the bundle
  // never mounted), because the pre-mount window is long when the JS
  // bundle is stale or slow. The 2026-09-04 dark-theme styling made
  // it look intentional, but it was still a full page of SEO copy
  // sitting where the app should be. The snapshot is now hidden from
  // sighted visitors (display:none) and a small branded loading
  // state shows instead; React replaces everything inside #root on
  // mount, so this only affects the pre-mount window. The snapshot
  // markup/text stays in the HTML for crawlers, and visitors without
  // JS still get the clean <noscript> snapshot in index.html.
    const styled = `<div class="ora-seo-snapshot">${inner}</div>` +
    `<div class="ora-loading"><div class="ora-loading-mark">ORA</div><div class="ora-loading-bar"><i></i></div><p>Loading your workspace&hellip;</p></div>` +
    `<style>
    .ora-seo-snapshot{display:none}
    .ora-loading{min-height:100vh;background:#0a0e1a;color:#f8fafc;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",Roboto,sans-serif}
    .ora-loading-mark{font-size:28px;font-weight:700;letter-spacing:-0.5px}
    .ora-loading-bar{width:120px;height:2px;background:#1e293b;overflow:hidden;border-radius:2px}
    .ora-loading-bar i{display:block;height:100%;width:40%;background:#4f46e5;animation:ora-loading-slide 1.2s ease-in-out infinite}
    @keyframes ora-loading-slide{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}
    .ora-loading p{color:#94a3b8;margin:0;font-size:15px}
    </style>`;
  const re = /(<div id="root">)([\s\S]*?)(<\/div>)/;
  if (re.test(html)) return html.replace(re, (_m, g1, _g2, g3) => `${g1}${styled}${g3}`);
  return html.replace('<div id="root"></div>', () => `<div id="root">${styled}</div>`);
}

function vsBody(c) {
  // 2026-09-22 · group mode ("vibe-coders" style entries have
  // `c.competitors` + `c.pickThemGroups` instead of a single
  // `c.name`/`c.pickThem`) — kept symmetric with VsPage.jsx's
  // generic rendering so the static SEO snapshot never drifts from
  // what a real visitor/crawler with JS sees.
  const rows = c.rows.map((row) => {
    const [l, a, ...them] = row;
    return `<tr><td>${esc(l)}</td><td>${esc(a)}</td>` +
      them.map((t) => `<td>${esc(t)}</td>`).join("") + `</tr>`;
  }).join("");
  const faq = c.faq.map(({ q, a }) =>
    `<details><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join("");
  const pick = (h, items) =>
    `<h2>${esc(h)}</h2><ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
  const others = Object.values(COMPETITORS)
    .filter((o) => o.slug !== c.slug)
    .map((o) => `<a href="/vs/${o.slug}">ORA vs ${esc(o.name)}</a>`)
    .join(" · ");
  const themHeader = c.isGroup
    ? c.competitors.map((comp) => `<th>${esc(comp.name)}</th>`).join("")
    : `<th>${esc(c.name)}</th>`;
  const pickThemBlocks = c.isGroup
    ? c.pickThemGroups.map((g) => pick(`Choose ${g.name} if you want…`, g.items)).join("")
    : pick(`Choose ${c.name} if you want…`, c.pickThem);
  return `<main>
<h1>ORA vs ${esc(c.name)}</h1>
<p>Honest comparison · data verified ${esc(LAST_VERIFIED)}.</p>
<p>${esc(c.intro)}</p>
<p>${esc(c.description)}</p>
${pick("Choose ORA if you want…", c.pickAurem)}
${pickThemBlocks}
<h2>The pricing difference, in real numbers</h2>
${c.pricingProse.map((p) => `<p>${esc(p)}</p>`).join("")}
<p><small>${esc(c.pricingFootnote)}</small></p>
<h2>Feature by feature</h2>
<table><thead><tr><th></th><th>ORA</th>${themHeader}</tr></thead>
<tbody>${rows}</tbody></table>
<h2>Frequently asked</h2>
${faq}
<h2>More comparisons</h2>
<p>${others} · <a href="/compare">All comparisons</a></p>
<p><a href="/signup">${TOKEN_MODE ? "Start free — one full scan, no credit card" : "Start free — 10 tasks/month, no credit card"}</a></p>
</main>`;
}

function compareBody() {
  // 2026-11-19 — render the full COMPETITORS data on the hub page:
  // every competitor's full intro and description, its comparison
  // rows table, and its FAQ (the same data the /vs pages render).
  const sections = Object.values(COMPETITORS).map((c) => {
    const rows = (c.rows || []).map((r) => `<tr><th>${esc(r[0])}</th><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join("");
    const faq = (c.faq || []).map((item) => `<h3>${esc(item.q)}</h3><p>${esc(item.a)}</p>`).join("");
    return `<section><h2><a href="/vs/${c.slug}">ORA vs ${esc(c.name)}</a></h2><p>${esc(c.intro)}</p><p>${esc(c.description)}</p><table><thead><tr><th></th><th>ORA</th><th>${esc(c.name)}</th></tr></thead><tbody>${rows}</tbody></table>${faq}</section>`;
  }).join("");
  return `<main>
<h1>How ORA compares</h1>
<p>${esc(COMPARE_HUB.description)}</p>
${sections}
<p><a href="/signup">${TOKEN_MODE ? "Start free — one full scan, no credit card" : "Start free — 10 tasks/month, no credit card"}</a></p>
</main>`;
}

function ldForVs(c) {
  return {
    "@context": "https://schema.org",
    "@graph": [
      { "@type": "FAQPage",
        mainEntity: c.faq.map(({ q, a }) => ({
          "@type": "Question", name: q,
          acceptedAnswer: { "@type": "Answer", text: a } })) },
      { "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "AUREM",
            item: "https://auremcto.com/" },
          { "@type": "ListItem", position: 2, name: "Compare",
            item: "https://auremcto.com/compare" },
          { "@type": "ListItem", position: 3,
            name: `ORA vs ${c.name}`, item: c.canonical } ] },
    ],
  };
}

const base = TOKEN_MODE
  ? patchStaleTokenPhrases(readFileSync(join(DIST, "index.html"), "utf8"))
  : readFileSync(join(DIST, "index.html"), "utf8");
let written = 0;

for (const c of Object.values(COMPETITORS)) {
  let html = swapHead(base, c);
  html = injectLd(html, ldForVs(c));
  html = injectRoot(html, vsBody(c));
  const dir = join(DIST, "vs", c.slug);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "index.html"), html);
  written += 1;
  console.log(`[seo-prerender] wrote /vs/${c.slug}/index.html`);
}

{
  let html = swapHead(base, COMPARE_HUB);
  html = injectLd(html, {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name: "ORA comparisons",
    itemListElement: Object.values(COMPETITORS).map((c, i) => ({
      "@type": "ListItem", position: i + 1,
      name: `ORA vs ${c.name}`, url: c.canonical })),
  });
  html = injectRoot(html, compareBody());
  mkdirSync(join(DIST, "compare"), { recursive: true });
  writeFileSync(join(DIST, "compare", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /compare/index.html");
}

// ── 2026-08-30 · P0 #1 — homepage / about / contact / privacy ───────

// Extra homepage copy from src/pages/Landing.jsx (extracted 2026-11-19).
// Renders landing sections the base snapshot omits, so served HTML carries real text.
// 2026-11-22 - five source import paths the 2026-11-19 extraction swept in were removed below; they are not copy and rendered as visible text in the snapshot.
const HOME_EXTRA = [
  "Fast, cheap, reliable.",
  "GPT-4o-mini / Claude Haiku 4.5",
  "class. Perfect for quick fixes and everyday tasks.",
  "\u2713 commit a1b2c3 pushed",
  "Balanced quality + depth.",
  "Claude Sonnet 4.5 / GPT-5.2",
  "class. Best default for production work.",
  "\u2713 commit 9z8y7x pushed",
  "Deep, thorough, audit-grade.",
  "Claude Opus 4.5 / GPT-5.2 / Gemini 3 Pro",
  ". For refactors and risky changes.",
  "\u2713 commit f4e3d2 pushed",
  "\u25b8 Developers Choice",
  "AUREM LOOP harness",
  "ORA reads your GitHub repo, runs Vanguard security scans,",
  "and ships production-ready code \u2014 with Loop Mode that",
  "verifies every step before committing.",
  "Watch it ship",
  "\u2014 no credit card required \u00b7 30-second signup",
  "Made in Canada",
  "GDPR \u00b7 PIPEDA \u00b7 DPDP \u00b7 CCPA aligned",
  "flat monthly",
  "tap to commit",
  "Why teams ship with ORA",
  "developers using ORA",
  "production commits shipped",
  "AI mistakes auto-caught",
  "Watch ORA ship real code",
  "Real repos. Real commits. No staging.",
  "\u25b6 OPEN FULL WALKTHROUGH \u2192",
  "New \u00b7 Vanguard 2.0",
  "surface sweep \u00b7 412 files \u00b7 25 patterns",
  "deep re-scan \u00b7 9 flagged \u00b7 context \u00b110",
  "sql_format + insecure_http \u2192 CRITICAL",
  "ORA wrote 3 patches \u00b7 risk 78 \u2192 12",
  "draft opened \u00b7 vanguard/auto-fix \u2713",
  "Vanguard 2.0 \u2014 two-round deep scan + auto PR",
  "Scan finds the bugs. ORA writes the fixes. PR lands in your",
  "repo \u2014 draft, never force-merged. New in Feb 2026.",
  "From prompt to commit in 4 steps",
  "No setup. No IDE. No context switching.",
  "The workspace",
  "Three windows. One agent.",
  "Code, Live Preview and Advisor \u2014 side by side. Switch tabs, never switch tools.",
  "Code \u00b7 auth.py",
  "DIFF +24 / \u22126",
  "# Iter 184 \u2014 bcrypt + rate-limit",
  "HTTPException",
  "create_token",
  "Live preview",
  "localhost:3000",
  "Welcome back",
  "Sign in to ship your next commit.",
  "hot reload \u00b7 142ms",
  "tests pass \u00b7 38 / 38",
  "Why are we using bcrypt over argon2 here?",
  "column. Want me to draft the PR?",
  "Yes \u2014 and keep bcrypt as fallback for older rows.",
  "planning across 3 files\u2026",
  "Three things no other AI dev tool does.",
  "Grep the repo. Every claim below points to a real file.",
  "CITATION GUARD",
  "file contents.",
  "If ORA mentions any file path or version WITHOUT reading it in the same",
  "turn, the response is auto-blocked, the real file is fetched, and the",
  "LLM is re-run with the correct data. Zero fabricated references \u2014 by",
  "design, not by prompt.",
  "services/citation_guard.py",
  "PARLIAMENT COUNCIL",
  "Three AI models vote",
  "before your code commits.",
  "core/parliament.py",
  "Your advisor sees",
  "what you see.",
  "services/advisor_vision.py",
  "Pick your speed",
  "Every mode commits direct to GitHub.",
  "Why teams switch",
  "Built like a teammate, not a chat bot",
  "All shipped, all in production.",
  "Why developers choose ORA",
  "GitHub Copilot",
  "Try ORA live \u2014 free.",
  "Connect your GitHub repo and ship your first task in under 2 minutes. No credit card required.",
  "What developers are saying",
  "Real feedback from real developers.",
  "Flat pricing. No surprises.",
  "Everything you need to know.",
  "Ready to ship code",
  "without an IDE?",
  "ORA by Aurem \u2014 Built for developers \u00b7 MIT extension",
  "Developer Portal",
  "OpenAPI Spec",
  "Privacy Policy",
  "Terms of Service",
  "Cookie Policy",
  "Acceptable Use",
  "Subprocessors",
  "System Status",
  "Support \u00b7 auremcto.com/support",
  "Privacy \u00b7 auremcto.com/support",
  "security@auremcto.com",
  "billing@auremcto.com",
  "Polaris Built Inc",
  "\u2014 ORA by Aurem, made by AUREM \u00b7 Incorporated in Canada \u00b7 All rights reserved.",
  "Cookie preferences",
  "~30 seconds end-to-end",
  "Minimum-diff commits",
  "Full review pass included",
  "Security scan every commit",
  "Multi-file reasoning",
  "Test scaffolds auto-included",
  "2-step plan-then-act",
  "Token-aware budget control",
  "Full repo grep + AST scan",
  "RCA on every regression",
  "Migration scripts included",
  "3-step plan/code/verify",
  "migrate sessions to redis",
  "UNIQUE \u2014 no competitor",
  "Security-First by Default",
  "25-pattern Vanguard scan runs before every commit. Secrets, injection, JWT replay, XSS \u2014 all caught before they reach your repo. The only AI engineer with mandatory pre-commit security.",
  "Loop Mode \u2014 Verified Before Ship",
  "Plan \u2192 Execute \u2192 Verify \u2192 Scan \u2192 Ship. ORA shows its plan, waits for your approval, runs ruff/eslint after every file, and only commits when everything passes. Self-heals on errors automatically, up to a 2-retry cap.",
  "Codebase Health Scanner",
  "16-category audit: security, performance, code quality, dependencies, database, and more. Health score 0-100. Individual fix buttons. Find 44 issues in minutes, fix them with one click.",
  "UNIQUE \u00b7 4-hop chain",
  "4-Provider Failover Chain",
  "OpenRouter \u2192 DeepSeek \u2192 OR free chain \u2192 Groq emergency. If one provider has an outage, ORA automatically retries the next in the chain \u2014 reducing (not eliminating) the chance that a single provider's downtime stops your ship.",
  "UNIQUE \u00b7 self-learning",
  "ORA Learns Your Codebase",
  "ORA Council tracks every interaction across 5 modes (chat, advice, code, debug, audit) and fine-tunes ORA on your specific patterns. The more you use ORA, the better it gets at your codebase.",
  "I shipped a Stripe integration in a Slack thread while waiting for a flight. ORA read the repo, wrote the code, ran the tests, committed. Mind blown.",
  "Finally an AI that actually commits \u2014 no copy-paste compromise. ORA is just direct.",
  "The only AI tool that understands my repo. After running it once, the warm-start memory is dialled in. Tasks land in minutes, not hours.",
  "Senior, SaaS startup",
  "Linux dev. No IDE, no problem. ORA via Claude Code in terminal is the cleanest workflow I've ever had.",
  "Ships changes to your GitHub repo",
  "Mobile-friendly (web + PWA)",
  "MCP server (Claude / Cursor / VS Code)",
  "Codebase memory (warm-start)",
  "OAuth 2.1 + PKCE for AI clients",
  "Open source friendly",
  "AUREM is the company. ORA is the product \u2014 an autonomous AI engineer that reads your GitHub repo, writes code, runs security scans, and commits directly to your branches. AUREM (Application No. 2492318 with the Canadian Intellectual Property Office) is a trademark of Polaris Built Inc.",
  "Does ORA commit code automatically or do I approve first?",
  "You approve first. ORA runs a 5-phase Loop \u2014 Plan \u2192 Execute \u2192 Verify \u2192 Scan \u2192 Ship \u2014 and Ship requires your manual click. Nothing lands in your repo until you press Ship, and every ship generates a real commit (not a force-push) that can be rolled back in one click.",
  "How much does ORA cost?",
  "What happens if ORA can't verify a change?",
  "ORA self-heals up to 2 times, then fails cleanly \u2014 no infinite loop. When the verify step (ruff / eslint / type checks) fails, ORA re-writes the file with the actual lint errors as context; if 2 attempts still fail, the loop terminates and shows you the exact ruff/eslint errors and which files failed so you can either fix manually or send a more specific follow-up prompt.",
  "No. ORA is a browser app (also installable as a PWA on mobile) and a terminal MCP 2.4 server \u2014 no IDE required. If you already live in VS Code, Cursor, Claude Desktop, or Claude Code, ORA has an MCP client for each, but the extension is optional.",
];
function homeExtraHtml() {
  return "<section><h2>More from ORA</h2>" + HOME_EXTRA.map((t) => "<p>" + t + "</p>").join("") + "</section>";
}

function homeBody() {
  return `<main>
<h1>ORA by AUREM \u2014 the AI engineer that actually commits</h1>
<p>ORA is an autonomous AI software engineer. It connects to your GitHub repository, plans a change, writes the code, runs a pre-commit security scan (Vanguard), and commits the result directly to your branch \u2014 only after your manual approval.</p>
<h2>How it works</h2>
<ul>
<li><strong>Plan</strong> \u2014 ORA shows what it will do; you approve first.</li>
<li><strong>Execute</strong> \u2014 files are written one at a time.</li>
<li><strong>Verify</strong> \u2014 lint/type checks run after every file, with a hard self-heal cap of 2 retries.</li>
<li><strong>Scan</strong> \u2014 Vanguard's pre-commit security patterns run automatically.</li>
<li><strong>Ship</strong> \u2014 nothing commits until you click Ship.</li>
</ul>
<h2>Pricing</h2>
<p>${TOKEN_MODE
    ? "One free full scan, no credit card. Starter: $9/month flat for 900 tokens \u2014 no per-action billing, unused tokens carry forward."
    : "Free: 10 tasks/month, no credit card. Starter: $9/month flat \u2014 no per-token billing."}</p>
${HARNESS_PAGE_MODE ? `<h2>Aurem Loop</h2>
<p>${HARNESS_RELEASE.available
    ? `A local coding harness that checks its own work. Download available \u2014 v${esc(HARNESS_RELEASE.version)} (free, Windows).`
    : "A local coding harness that checks its own work. Not available to download yet."}</p>
${HARNESS_RELEASE.available ? `<p><a href="/harness">Download Aurem Loop</a></p>\n` : ""}<p><a href="/harness">Learn about Aurem Loop \u2192</a></p>
` : ""}<p><a href="/about">About AUREM</a> \u00b7 <a href="/compare">How ORA compares</a> \u00b7 <a href="/pricing">Pricing</a> \u00b7 <a href="/developers">Developers</a> \u00b7 <a href="/api/docs">API Docs</a> \u00b7 <a href="/signup">Start free</a></p>
</main>`;
}

function aboutBody() {
  const paras = ABOUT.paragraphs.map((p) => `<p>${esc(p)}</p>`).join("");
  const links = ABOUT.links
    .map((l) => `<a href="${esc(l.href)}">${esc(l.label)}</a>`)
    .join(" \u00b7 ");
  return `<main>
<h1>${esc(ABOUT.heading)}</h1>
${paras}
<h2>Links</h2>
<p>${links}</p>
<p><a href="/contact">Contact</a> \u00b7 <a href="/privacy">Privacy</a> \u00b7 <a href="/terms">Terms</a></p>
</main>`;
}

function contactBody() {
  const paras = CONTACT.paragraphs.map((p) => `<p>${esc(p)}</p>`).join("");
  return `<main>
<h1>${esc(CONTACT.heading)}</h1>
${paras}
<p><a href="/support">Open a support ticket</a></p>
<p><a href="/about">About</a> \u00b7 <a href="/privacy">Privacy</a> \u00b7 <a href="/terms">Terms</a></p>
</main>`;
}

function privacyBody() {
  const md = readFileSync(
    join(ROOT, "public", "policies", "privacy-policy.md"), "utf8",
  );
  return `<main>${marked.parse(md)}</main>`;
}
function termsBody() {  const md = readFileSync(    join(ROOT, "public", "policies", "terms-of-service.md"), "utf8",  );  return `<main>${marked.parse(md)}</main>`;}
// 2026-09-12 · is-agentic.com agent-readiness fixes — /developers
// needs the same real-HTML-without-JS treatment as /about, /contact.
function developersBody() {
  const sections = DEVELOPERS.sections.map((s) => {
    const links = s.links
      .map((l) => `<li><a href="${esc(l.href)}">${esc(l.label)}</a></li>`)
      .join("");
    return `<h2>${esc(s.heading)}</h2><p>${esc(s.body)}</p><ul>${links}</ul>`;
  }).join("");
  const intro = DEVELOPERS.intro.map((p) => `<p>${esc(p)}</p>`).join("");
  return `<main>
<h1>${esc(DEVELOPERS.heading)}</h1>
${intro}
${sections}
<p><a href="/about">About</a> \u00b7 <a href="/contact">Contact</a> \u00b7 <a href="/privacy">Privacy</a></p>
</main>`;
}

// Pricing plans mirror src/components/PricingCards.jsx (Free/Starter/Pro/Team).
// Keep the names, prices and features in sync with that file.
const PRICING_PLANS = [
  {
    id: "free",
    name: "Free",
    price: "$0",
    period: "/ month USD",
    cta: "Start free",
    features: [
      "10 tasks per month",
      "Standard mode",
      "Changes delivered to your repo",
      "F12 error debugger",
      "Community support",
    ],
  },
  {
    id: "starter",
    name: "Starter",
    price: "$9",
    period: "/ month USD",
    cta: "Upgrade to Starter",
    features: [
      "50 tasks per month",
      "Swift mode only",
      "Changes delivered to your repo with one-click rollback",
      "Live worker tape",
      "Email support",
    ],
  },
  {
    id: "pro",
    name: "Pro",
    price: "$19",
    period: "/ month USD",
    cta: "Upgrade to Pro",
    features: [
      "300 tasks per month",
      "Swift + Pro review modes",
      "Changes delivered to your repo — Beta",
      "Project Brain memory",
      "Parallel agents",
      "VS Code extension",
      "Priority support",
    ],
  },
  {
    id: "team",
    name: "Team",
    price: "$49",
    period: "/ month USD",
    cta: "Upgrade to Team",
    features: [
      "Everything in Pro",
      "400 tasks per month",
      "Maxx mode — Claude writes your code",
      "Changes delivered to your repo — Beta",
      "All 3 modes: Swift, Pro, Maxx",
      "Admin dashboard + roles",
      "Priority queue",
      "Shared project brain",
      "Slack / DM support",
    ],
  },
];

function pricingBody() {
  // 2026-11-19 — full pricing snapshot: plan cards, a feature comparison
  // table built from PRICING_PLANS, the full FAQ, and the site footer.
  // tierCapacityLine() returns null when the tasks wording is active
  // (TOKEN_WORDING_ACTIVE off), so a missing capacity line is omitted
  // instead of rendering the literal string "null".
  const plans = PRICING_PLANS.map((pp) => {
    const feats = pp.features.map((f) => "<li>" + f + "</li>").join("");
    const cap = tierCapacityLine(pp.id);
    const tag = pp.id === "pro" ? "<p>Most popular</p>" : "";
    return `<section><h2>${pp.name} — ${pp.price} ${pp.period}</h2>` + tag + `<ul>` + feats + "</ul><p>" + pp.cta + "</p>" + (cap ? "<p>" + cap + "</p>" : "") + "</section>";
  }).join("");
  const featureSet = [];
  PRICING_PLANS.forEach((pp) => pp.features.forEach((f) => { if (f !== "Everything in Pro" && !featureSet.includes(f)) featureSet.push(f); }));
  const tableRows = featureSet.map((f) => {
    const cells = PRICING_PLANS.map((pp) => `<td>${pp.features.includes(f) ? "Yes" : "—"}</td>`).join("");
    return `<tr><th>${esc(f)}</th>${cells}</tr>`;
  }).join("");
  const tableHtml = `<section><h2>Compare plans</h2><table><thead><tr><th>Feature</th><th>Free</th><th>Starter</th><th>Pro</th><th>Team</th></tr></thead><tbody>${tableRows}</tbody></table><p>Monthly price: Free $0, Starter $9, Pro $19, Team $49. Tasks per month: 10 on Free, 50 on Starter, 300 on Pro, 400 on Team. Annual billing saves 20%.</p></section>`;
  const faq = [
    { q: "How much does ORA cost?", a: howMuchFaqAnswer() },
    { q: "Is there a free plan?", a: "Yes. The Free plan includes 10 tasks per month, Standard mode, changes delivered to your repo, the F12 error debugger, and community support — and you can start with no credit card." },
    { q: "What counts as a task?", a: "A task is one job ORA completes and delivers to your repo. Each plan includes a monthly task allotment: 10 tasks on Free, 50 on Starter, 300 on Pro, and 400 on Team. Your allotment renews each month." },
    { q: "Are there per-seat or per-token charges?", a: "No. ORA charges one flat monthly price per plan — $9, $19 or $49 — with no per-seat pricing and no token meters to watch." },
    { q: "Do I need to install an IDE or extension?", a: "No. ORA works outside the editor and commits directly to GitHub, so there is no IDE to install and no extension to configure." },
    { q: "What are Swift, Pro, and Maxx modes?", a: "Swift is the fast standard mode. Pro adds review modes that double-check the work before it ships. Maxx, on Team, has Claude write your code directly. Free includes Standard mode, Starter includes Swift mode only, Pro includes Swift and Pro review modes, and Team includes all three modes. Starter and up include the live worker tape so you can watch ORA work." },
    { q: "What is Project Brain?", a: "Project Brain is ORA's memory of your project across tasks. It is included on Pro, and Team adds a shared project brain for the whole team." },
    { q: "How does ORA deliver changes?", a: "ORA commits changes directly to your repo. The Starter plan adds one-click rollback on every delivery." },
    { q: "Is there an annual discount?", a: "Yes. Annual plans save 20%: Starter $86 per year, Pro $182 per year, Team $470 per year." },
    { q: "Can I cancel anytime?", a: "Yes. ORA is a flat monthly fee with no token surprises, and you can cancel any time. You manage your subscription from the billing portal." },
    { q: "What is the F12 error debugger?", a: "The F12 error debugger helps diagnose errors in your app. It is included on the Free plan." },
  ];
  const faqHtml = `<section><h2>Frequently asked questions</h2>` + faq.map((item) => `<h3>${esc(item.q)}</h3><p>${esc(item.a)}</p>`).join("") + `</section>`;
  const footerHtml = `<p><a href="/about">About AUREM</a> · <a href="/compare">How ORA compares</a> · <a href="/pricing">Pricing</a> · <a href="/developers">Developers</a> · <a href="/api/docs">API Docs</a> · <a href="/signup">Start free</a> · <a href="/contact">Contact</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></p><p>Cookie preferences</p><p>Still have questions? <a href="/contact">Contact us</a>.</p>`;
  return `<main><h1>Pricing</h1><p>` + pricingHeroHeadline() + `</p><h2>Plans</h2><p>` + pricingDetailLine() + `</p><p>How ORA works: ORA connects to your GitHub repo, writes code, runs a security scan, and commits on your approval. Every plan delivers changes directly to your repo.</p><p>Try it free: one free full scan, no credit card.</p>` + plans + `<section><h2>` + comparisonSectionSub() + `</h2><p>` + comparisonFlatPricingRowLabel() + `</p><p>` + pricingFeatureCardDesc() + `</p></section>` + tableHtml + faqHtml + `<p>` + landingPricingSectionSub() + `</p>` + footerHtml + `</main>`;
}

{
  let html = swapHead(base, {
    title: "ORA by AUREM \u2014 the AI engineer that actually commits",
    description: TOKEN_MODE
      ? "ORA connects to your GitHub repo, writes code, runs a security " +
        "scan, and commits on your approval. One free full scan, no " +
        "card. Starter: $9/month flat for 900 tokens."
      : "ORA connects to your GitHub repo, writes code, runs a security " +
        "scan, and commits on your approval. Free tier: 10 tasks/month, " +
        "no card. Starter: $9/month flat.",
    canonical: "https://auremcto.com/",
  });
  html = injectRoot(html, homeBody() + homeExtraHtml());
  writeFileSync(join(DIST, "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote / (root index.html, homepage content)");
}

{
  let html = swapHead(base, ABOUT);
  html = injectRoot(html, aboutBody());
  mkdirSync(join(DIST, "about"), { recursive: true });
  writeFileSync(join(DIST, "about", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /about/index.html");
}

{
  let html = swapHead(base, CONTACT);
  html = injectRoot(html, contactBody());
  mkdirSync(join(DIST, "contact"), { recursive: true });
  writeFileSync(join(DIST, "contact", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /contact/index.html");
}

{
  let html = swapHead(base, {
    title: "Privacy Policy — AUREM",
    description: "AUREM's privacy policy: GDPR/PIPEDA/DPDP disclosures, cookie policy, data-retention windows, subprocessor list.",
    canonical: "https://auremcto.com/privacy",
  });
  html = injectRoot(html, privacyBody());
  mkdirSync(join(DIST, "privacy"), { recursive: true });
  writeFileSync(join(DIST, "privacy", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /privacy/index.html");
}
  {    let html = swapHead(base, {      title: "Terms of Service — AUREM",      description: "AUREM terms of service: service description, eligibility, GitHub access, AI-generated code, acceptable use, subscriptions and billing, data and privacy, liability and governing law.",      canonical: "https://auremcto.com/terms",    });    html = injectRoot(html, termsBody());    mkdirSync(join(DIST, "terms"), { recursive: true });    writeFileSync(join(DIST, "terms", "index.html"), html);    written += 1;    console.log("[seo-prerender] wrote /terms/index.html");  }
{
  let html = swapHead(base, DEVELOPERS);
  html = injectRoot(html, developersBody());
  mkdirSync(join(DIST, "developers"), { recursive: true });
  writeFileSync(join(DIST, "developers", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /developers/index.html");
}

  {
    let html = swapHead(base, {
      title: "Pricing — AUREM",
      description: "AUREM pricing: flat monthly plans with no token meters and no per-seat pricing. Free, Starter, Pro and Team plans.",
      canonical: "https://auremcto.com/pricing",
    });
    html = injectRoot(html, pricingBody());
    mkdirSync(join(DIST, "pricing"), { recursive: true });
    writeFileSync(join(DIST, "pricing", "index.html"), html);
    written += 1;
    console.log("[seo-prerender] wrote /pricing/index.html");
  }

// 2026-11-17 — rebuilt for the Midnight Gold /harness page. The page
// is now fully self-contained (hero + inline sign-up/sign-in card +
// features + FAQ + Licensing & Policies + footer, all driven by
// src/data/harnessContent.mjs — see that module's own docstring). This
// function builds the crawler-facing static snapshot from the SAME
// import, so the two can never show different copy. The only
// per-build-varying piece is the release itself (version/sha256/
// size_bytes), read from HARNESS_RELEASE (GET /harness/release-status,
// resolved above). ONLY written when HARNESS_PAGE_MODE is true — when
// the flag is off, no dedicated dist/harness/index.html is written at
// all; a no-JS visitor hitting /harness falls back to the generic SPA
// shell (dist/index.html), same as any other unmatched route.
function fmtBytes(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

// `published_at` on GET /api/harness/release-status is a Unix epoch
// (seconds). Formats both an ISO date (for JSON-LD `datePublished`)
// and a human-readable one (for the visible download-proof text) —
// always derived from the REAL release row, never a hardcoded date.
function releaseDates(publishedAt) {
  if (typeof publishedAt !== "number" || !Number.isFinite(publishedAt)) {
    return { iso: null, human: null };
  }
  const d = new Date(publishedAt * 1000);
  const iso = d.toISOString().slice(0, 10);
  const human = d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
  return { iso, human };
}

function harnessBody(release) {
  const pointsHtml = POINTS.map((pt) => `<li><b>${esc(pt.b)}</b> — ${esc(pt.t)}</li>`).join("");
  const featuresHtml = FEAT_ITEMS.map((f) => `<li><b>${esc(f.b)}</b> — ${esc(f.p)}</li>`).join("");
  const flowHtml = FLOW_STEPS.map((s) => `<li><b>${esc(s.num)}: ${esc(s.b)}</b> — ${esc(s.p)}</li>`).join("");
  const requirementsHtml = REQUIREMENTS.map((itemHtml) => `<li>${itemHtml}</li>`).join("");
  const faqHtml = FAQ_ITEMS.map((f) =>
    `<details><summary>${esc(f.q)}</summary><p>${f.aHtml}</p></details>`,
  ).join("");
  const legalHtml = LEGAL_ARTICLES.map((art) =>
    `<h3 id="${esc(art.id)}">${esc(art.title)}</h3><p>${esc(art.updated)}</p>${art.bodyHtml}`,
  ).join("");
  const footerColsHtml = FOOTER_COLS.map((col) =>
    `<nav aria-label="${esc(col.label)}"><h3>${esc(col.label)}</h3><ul>${
      col.links.map((l) => `<li><a href="${esc(l.href)}">${esc(l.t)}</a></li>`).join("")
    }</ul></nav>`,
  ).join("");

  const downloadHtml = release.available
    ? `<section id="download-proof">
<h2>Download Aurem Loop — FREE</h2>
<p><b>Version ${esc(release.version)}</b> · Released ${esc(releaseDates(release.published_at).human || "")}</p>
<p>File: ${esc(fmtBytes(release.size_bytes))} (${esc(release.size_bytes)} bytes) · SHA-256: <code>${esc(release.sha256)}</code></p>
<p>Ships qwen2.5-coder:1.5b via Ollama — a small model that runs entirely on your PC. Platform: ${esc(release.platform === "windows" ? "Windows" : (release.platform || "Windows"))}.</p>
</section>`
    : `<p>The Windows download is not available yet.</p>`;

  const installHtml = `<section id="install">
<p class="kicker">${esc(INSTALL.kicker)}</p>
<h2>${esc(INSTALL.title)}</h2>
<p>${esc(INSTALL.lede)}</p>
<p><b>${esc(INSTALL.oneCommandLabel)}</b></p>
<pre>${esc(INSTALL.oneCommand)}</pre>
<p><b>${esc(INSTALL.manualTitle)}</b></p>
<ol>${INSTALL.manualSteps.map((stepHtml) => `<li>${stepHtml}</li>`).join("")}</ol>
</section>`;

  const { iso: releaseIso } = releaseDates(release.published_at);
  const ld = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "FAQPage",
        mainEntity: FAQ_ITEMS.map((f) => ({
          "@type": "Question",
          name: f.q,
          acceptedAnswer: { "@type": "Answer", text: stripHtml(f.aHtml) },
        })),
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "AUREM", item: "https://auremcto.com/" },
          { "@type": "ListItem", position: 2, name: "Aurem Loop", item: "https://auremcto.com/harness" },
        ],
      },
      // 2026-11-18 — AAO content audit, Fix 3: SoftwareApplication
      // for Aurem Loop. Only emitted once a release is actually
      // live — never asserts a version/download that doesn't exist,
      // every fact pulled straight from the real release-status row.
      ...(release.available ? [{
        "@type": "SoftwareApplication",
        name: "Aurem Loop",
        applicationCategory: "DeveloperApplication",
        operatingSystem: release.platform === "windows" ? "Windows" : (release.platform || "Windows"),
        url: "https://auremcto.com/harness",
        downloadUrl: "https://auremcto.com/harness",
        softwareVersion: release.version,
        ...(releaseIso ? { datePublished: releaseIso } : {}),
        description: `${HERO.sub} Runs entirely on your PC. Ships qwen2.5-coder:1.5b.`,
        provider: {
          "@type": "Organization", name: "Ora by Aurem",
          legalName: "Polaris Built Inc.", url: "https://auremcto.com",
        },
        offers: { "@type": "Offer", price: "0", priceCurrency: "USD", availability: "https://schema.org/InStock" },
      }] : []),
    ],
  };

  return {
    body: `<main>
<p>${esc(HERO.brandLine)}</p>
<h1>${HERO.headlineLines.map(esc).join(" ")} <span>${esc(HERO.headlineGold)}</span></h1>
<p>${esc(HERO.sub)}</p>
<ul>${pointsHtml}</ul>
<p>${esc(BENCH.passed)} ${esc(BENCH.passedLabel)} · ${esc(BENCH.bareLabel)} ${esc(BENCH.bare)} · ${esc(BENCH.falseAcceptsLabel)} ${esc(BENCH.falseAccepts)}</p>
<p>${esc(BENCH.note)}</p>
${downloadHtml}
${release.available ? installHtml : ""}
<h2>Features</h2>
<ul>${featuresHtml}</ul>
<h2>How it works</h2>
<ol>${flowHtml}</ol>
<h2>System requirements</h2>
<ul>${requirementsHtml}</ul>
<h2>FAQ</h2>
${faqHtml}
<h2>Licensing &amp; policies</h2>
${legalHtml}
<footer>
<p>${esc(FOOTER_TAGLINE)}</p>
${footerColsHtml}
<p>${esc(FOOTER_COPY)}</p>
<p>${esc(FOOTER_TRADEMARK)}</p>
<p>Last updated: ${new Date().toISOString().slice(0, 10)}</p>
</footer>
</main>`,
    ld,
    meta: {
      title: "Aurem Loop — your code, your PC, your model",
      description: HERO.sub,
      canonical: "https://auremcto.com/harness",
    },
  };
}

if (HARNESS_PAGE_MODE) {
  const { body, ld, meta } = harnessBody(HARNESS_RELEASE);
  let html = swapHead(base, meta);
  html = injectRoot(html, body);
  html = injectLd(html, ld);
  mkdirSync(join(DIST, "harness"), { recursive: true });
  writeFileSync(join(DIST, "harness", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /harness/index.html (harness_page flag ON)");
} else {
  console.log("[seo-prerender] skipped /harness/index.html (flag OFF — page stays hidden)");
}
console.log(`[seo-prerender] done — ${written} snapshots.`);
