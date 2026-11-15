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
const { FAQ } = await import("../src/data/faq.mjs");

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

// 2026-11-10 — Phase A content now lives in the private AuremHQ/aurem-loop
// repo (services/harness_content_repo.py on the backend); this build-time
// script fetches it through the SAME GET /api/harness/content a real
// visitor's browser calls, so the static snapshot can never show
// different copy than the live page. If the flag is on but the
// content isn't available yet (nobody has pushed the content file to
// the harness repo yet, or the fetch fails), no dedicated
// dist/harness/index.html is written — same "stays hidden" fallback
// as the flag being off.
async function resolveHarnessContent() {
  if (!HARNESS_PAGE_MODE) return null;
  const backendUrl =
    process.env.VITE_API_URL ||
    _loadedEnv.VITE_API_URL ||
    process.env.REACT_APP_BACKEND_URL ||
    _loadedEnv.REACT_APP_BACKEND_URL;
  if (!backendUrl) return null;
  try {
    const resp = await fetch(`${backendUrl}/api/harness/content`, {
      signal: AbortSignal.timeout(8000),
    });
    if (!resp.ok) return null;
    const body = await resp.json();
    const content = body?.content;
    if (!content || content.available === false) return null;
    return { content, liveStats: body?.live_stats || null };
  } catch (e) {
    console.warn(`[seo-prerender] harness/content fetch failed (${e.message}) — /harness snapshot skipped`);
    return null;
  }
}
const HARNESS_CONTENT = await resolveHarnessContent();

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

function swapHead(html, { title, description, canonical }) {
  let out = html
    .replace(/<title>[\s\S]*?<\/title>/, `<title>${esc(title)}</title>`)
    .replace(/(<meta\s+name="description"\s+content=")[^"]*(")/,
      `$1${esc(description)}$2`)
    .replace(/(<meta\s+property="og:title"\s+content=")[^"]*(")/,
      `$1${esc(title)}$2`)
    .replace(/(<meta\s+property="og:description"\s+content=")[^"]*(")/,
      `$1${esc(description)}$2`)
    .replace(/(<meta\s+property="og:url"\s+content=")[^"]*(")/,
      `$1${esc(canonical)}$2`)
    .replace(/(<meta\s+name="twitter:title"\s+content=")[^"]*(")/,
      `$1${esc(title)}$2`)
    .replace(/(<meta\s+name="twitter:description"\s+content=")[^"]*(")/,
      `$1${esc(description)}$2`);
  if (/<link\s+rel="canonical"/.test(out)) {
    out = out.replace(/(<link\s+rel="canonical"\s+href=")[^"]*(")/,
      `$1${esc(canonical)}$2`);
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
  const styled = `<div class="ora-seo-snapshot">${inner}</div>` +
    `<style>
      .ora-seo-snapshot{background:#0a0e1a;color:#f8fafc;min-height:100vh;
        font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",Roboto,sans-serif;
        max-width:760px;margin:0 auto;padding:64px 24px;line-height:1.6}
      .ora-seo-snapshot h1{font-size:28px;font-weight:700;margin:0 0 18px;letter-spacing:-0.5px}
      .ora-seo-snapshot h2{font-size:20px;font-weight:700;margin:32px 0 12px}
      .ora-seo-snapshot p{color:#94a3b8;margin:0 0 14px;font-size:15px}
      .ora-seo-snapshot ul,.ora-seo-snapshot ol{margin:0 0 14px;padding-left:22px;color:#94a3b8;font-size:15px}
      .ora-seo-snapshot li{margin-bottom:8px}
      .ora-seo-snapshot a{color:#f59e0b;text-decoration:none}
      .ora-seo-snapshot strong{color:#f8fafc}
      .ora-seo-snapshot table{width:100%;border-collapse:collapse;margin:0 0 14px;font-size:14px}
      .ora-seo-snapshot th,.ora-seo-snapshot td{padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.08);text-align:left}
    </style>`;
  const re = /(<div id="root">)([\s\S]*?)(<\/div>)/;
  if (re.test(html)) return html.replace(re, `$1${styled}$3`);
  return html.replace('<div id="root"></div>', `<div id="root">${styled}</div>`);
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
  const cards = Object.values(COMPETITORS).map((c) =>
    `<li><a href="/vs/${c.slug}">ORA vs ${esc(c.name)}</a> — ${esc(c.intro.slice(0, 150))}…</li>`)
    .join("");
  return `<main>
<h1>How ORA compares</h1>
<p>${esc(COMPARE_HUB.description)}</p>
<ul>${cards}</ul>
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
<p>A local coding harness that checks its own work. Not available to download yet.</p>
<p><a href="/harness">Learn about Aurem Loop \u2192</a></p>
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
  html = injectRoot(html, homeBody());
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

{
  let html = swapHead(base, DEVELOPERS);
  html = injectRoot(html, developersBody());
  mkdirSync(join(DIST, "developers"), { recursive: true });
  writeFileSync(join(DIST, "developers", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /developers/index.html");
}

// 2026-11-12 · /faq — AlsoAsked FAQ page (11 questions people actually
// search). Same drift-proof pattern as /about: content lives in
// src/data/faq.mjs, rendered by FaqPage.jsx and by this snapshot.
{
  const faqItems = FAQ.items.map((f) =>
    `<details><summary>${esc(f.q)}</summary><p>${esc(f.a)}</p></details>`,
  ).join("");
  const faqLd = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "FAQPage",
        mainEntity: FAQ.items.map((f) => ({
          "@type": "Question",
          name: f.q,
          acceptedAnswer: { "@type": "Answer", text: f.a },
        })),
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "AUREM", item: "https://auremcto.com/" },
          { "@type": "ListItem", position: 2, name: "FAQ", item: "https://auremcto.com/faq" },
        ],
      },
    ],
  };
  let html = swapHead(base, FAQ);
  html = injectRoot(html, `<main>
<h1>${esc(FAQ.heading)}</h1>
<p>${esc(FAQ.intro)}</p>
${faqItems}
<p><a href="/about">About</a> \u00b7 <a href="/pricing">Pricing</a> \u00b7 <a href="/contact">Contact</a></p>
</main>`);
  html = injectLd(html, faqLd);
  mkdirSync(join(DIST, "faq"), { recursive: true });
  writeFileSync(join(DIST, "faq", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /faq/index.html");
}

// 2026-11-09 — /harness itself (not just the homepage promo blurb
// above). ONLY written when HARNESS_PAGE_MODE is true — the same
// build-time flag read. When the flag is off, no dedicated
// dist/harness/index.html is written at all; a no-JS visitor hitting
// /harness falls back to the generic SPA shell (dist/index.html),
// same as any other unmatched route — "flag off means the page stays
// hidden" holds trivially, there is no harness content baked in
// anywhere in that build.
function harnessBody(content, liveStats) {
  const howItWorks = (content.setup?.steps || [])
    .map((s) => `<li>${esc(s)}</li>`).join("");
  const whatItIsNot = (content.whatItIsNot || [])
    .map((s) => `<li>${esc(s)}</li>`).join("");
  const featureTags = (content.featureTags || [])
    .map((s) => `<li>${esc(s)}</li>`).join("");
  const whoNot = (content.whoShouldNotPick || [])
    .map((s) => `<li>${esc(s)}</li>`).join("");
  const troubleshooting = (content.troubleshooting || []).map((t) =>
    `<details><summary>${esc(t.q)}</summary><p>${esc(t.a)}</p></details>`,
  ).join("");
  const faqItems = (content.faq || []).map((f) =>
    `<details><summary>${esc(f.q)}</summary><p>${esc(f.a)}</p></details>`,
  ).join("");
  const compareRows = Object.entries(content.compare?.auremRow || {})
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
  const mailtoHref = `mailto:${esc(content.phaseA?.waitlistEmail || "polarisbuiltinc@gmail.com")}` +
    `?subject=${encodeURIComponent(content.phaseA?.waitlistSubject || "Aurem Loop waitlist")}` +
    `&body=${encodeURIComponent(content.phaseA?.waitlistBody || "")}`;
  const stars = liveStats?.stars ?? "\u2014";
  const forks = liveStats?.forks ?? "\u2014";

  const ld = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "FAQPage",
        mainEntity: (content.faq || []).map((f) => ({
          "@type": "Question",
          name: f.q,
          acceptedAnswer: { "@type": "Answer", text: f.a },
        })),
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "AUREM", item: "https://auremcto.com/" },
          { "@type": "ListItem", position: 2, name: "Aurem Loop", item: "https://auremcto.com/harness" },
        ],
      },
    ],
  };
  return {
    body: `<main>
<p>${(content.badges || []).map(esc).join(" \u00b7 ")}</p>
<h1>${esc(content.hero?.headline)}</h1>
<p>${esc(content.hero?.subhead)}</p>
<p>${esc(content.hero?.body)}</p>
<p><strong>PHASE A</strong> \u2014 ${esc(content.phaseA?.banner)}</p>
<p><a href="${mailtoHref}">Join the waitlist</a></p>
<ul>${featureTags}</ul>
<h2>${esc(content.setApart?.headline)}</h2>
<p>${esc(content.setApart?.wspKit?.badge)} \u2014 ${esc(content.setApart?.wspKit?.body)}</p>
<p>${esc(content.setApart?.phoneTunnel?.badge)} \u2014 ${esc(content.setApart?.phoneTunnel?.body)}</p>
<h2>${esc(content.githubStats?.repoLabel)}</h2>
<p>${esc(content.githubStats?.note)}</p>
<p>Stars (live): ${esc(String(stars))} \u00b7 Forks (live): ${esc(String(forks))} \u00b7 Clones: ${esc(String(content.githubStats?.clones))} \u00b7 Views: ${esc(String(content.githubStats?.views))} (last verified ${esc(content.githubStats?.lastVerified)})</p>
<h2>What it is not</h2>
<ul>${whatItIsNot}</ul>
<h2>${esc(content.compare?.headline)}</h2>
<p>${esc(content.compare?.body)}</p>
<table><tbody>${compareRows}</tbody></table>
<p>${esc(content.compare?.competitorNote)}</p>
<h2>Who should not pick Aurem Loop</h2>
<ul>${whoNot}</ul>
<h2>${esc(content.usersSay?.headline)}</h2>
<p>${esc(content.usersSay?.body)}</p>
<h2>${esc(content.setup?.headline)}</h2>
<ol>${howItWorks}</ol>
<h2>Troubleshooting</h2>
${troubleshooting}
<h2>FAQ</h2>
${faqItems}
<p>${esc(content.footer?.tagline)} ${esc(content.footer?.legal)}</p>
<p><a href="/about">About</a> \u00b7 <a href="/contact">Contact</a> \u00b7 <a href="/privacy">Privacy</a></p>
</main>`,
    ld,
    meta: { title: content.title, description: content.description, canonical: content.canonical },
  };
}

if (HARNESS_CONTENT) {
  const { body, ld, meta } = harnessBody(HARNESS_CONTENT.content, HARNESS_CONTENT.liveStats);
  let html = swapHead(base, meta);
  html = injectRoot(html, body);
  html = injectLd(html, ld);
  mkdirSync(join(DIST, "harness"), { recursive: true });
  writeFileSync(join(DIST, "harness", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /harness/index.html (harness_page flag ON, content available)");
} else {
  console.log("[seo-prerender] skipped /harness/index.html (flag OFF or content not yet available — page stays hidden)");
}

console.log(`[seo-prerender] done — ${written} snapshots.`);
