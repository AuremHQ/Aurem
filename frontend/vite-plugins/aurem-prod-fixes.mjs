/**
 * vite-plugins/aurem-prod-fixes.mjs
 *
 * 2026-08-30 · CRITICAL correction, same round as the public-site
 * "agentic readiness" fixes: Emergent's OWN production deploy for
 * this app runs the exact same `yarn start` (Vite DEV server,
 * `vite --port 3000`) as this preview pod — confirmed via
 * deployment_agent, twice. It does NOT run `yarn build`, so:
 *   - scripts/seo-prerender.mjs (wired only into the "build" script)
 *     never executes on Emergent's production path.
 *   - The `frontend/Dockerfile` nginx real-404 config belongs to a
 *     SEPARATE, unused "Hybrid Standalone" self-hosted stack — not
 *     what serves auremcto.com.
 * Net effect: the P0 #1 (no-JS static content) and P0 #2 (real 404)
 * fixes built as build-time artifacts would have been DEAD CODE on
 * the actual Emergent deploy. This plugin re-implements both fixes
 * as Vite dev-server middleware / transformIndexHtml hooks, so they
 * take effect on the SAME command Emergent actually runs in prod.
 *
 * The seo-prerender.mjs build-time version is UNCHANGED and still
 * correct for the separate Hybrid Standalone Docker/nginx stack, if
 * that's ever used — this plugin is the Emergent-path twin, reusing
 * the identical content sources (src/data/companyInfo.mjs, the real
 * privacy-policy.md) so the two can never show different text.
 *
 * 2026-10-06 · RE-OPENED by a code review after a founder-reported
 * production bug: the live homepage was stuck showing the static
 * snapshot instead of the real app. The visible snapshot's footer
 * link row had 6 links (About/Compare/Pricing/Developers/API
 * Docs/Start free) with the `dist/index.html` build's exact embedded
 * dark-theme CSS — this plugin's own `homeBody()` below only emits 4
 * links with NO styling at all, so what the founder saw does not
 * match what this dev-server plugin would have produced. That means
 * the "confirmed twice" claim above may no longer hold — something
 * may have changed the actual prod serving path to `yarn build` +
 * static `dist/` since 2026-08-30. A deploy-agent debug pass has been
 * requested to re-confirm the real prod command before this comment
 * (and the "dead code" claim about scripts/seo-prerender.mjs /
 * Dockerfile below) is trusted again either way.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { ABOUT, CONTACT } from "../src/data/companyInfo.mjs";
import { DEVELOPERS } from "../src/data/developerInfo.mjs";

// Mirrors frontend/Dockerfile's nginx allowlist regex EXACTLY — keep
// both lists in sync when routes are added/removed in src/App.jsx.
const KNOWN_PREFIXES = new Set([
  "both", "why-ora", "demo", "login", "signup", "reset-password", "verify",
  "support", "dashboard", "ora", "build", "integrations", "deploy", "domain",
  "settings", "profile", "tokens", "analytics", "projects", "admin", "tools",
  "feature-window", "codebase-health", "health", "bug-hunt",
  "sidebar-preview", "dashboard-preview-v2", "privacy", "terms",
  "acceptable-use", "cookie-policy", "cookie-preferences", "refund-policy",
  "ai-code-processing", "subprocessors", "dpa", "security", "status",
  "policies", "wall", "wrapped", "automations", "oauth-finish",
  "magic-login", "vs", "pricing", "compare", "dev", "about", "contact",
  // 2026-09-12 · is-agentic.com agent-readiness fix — new /developers
  // portal (see ROUTE_META below for its no-JS server-rendered body).
  "developers",
  // 2026-02 · Phase 1 (Team RBAC) — /settings?tab=team is covered by
  // the existing "settings" prefix, but /team/accept-invite (public
  // invite-acceptance page, App.jsx) is its own top-level route.
  "team",
  // Admin Team S3 (AT2) — /team-invite/accept standalone invite-accept
  // page (frontend/src/pages/TeamInviteAccept.jsx).
  "team-invite",
]);

const HAS_EXTENSION = /\.[a-zA-Z0-9]+$/;

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function homeBody() {
  return `<main>
<h1>ORA by AUREM — the AI engineer that actually commits</h1>
<p>ORA is an autonomous AI software engineer. It connects to your GitHub repository, plans a change, writes the code, runs a pre-commit security scan (Vanguard), and commits the result directly to your branch — only after your manual approval.</p>
<h2>How it works</h2>
<ul>
<li><strong>Plan</strong> — ORA shows what it will do; you approve first.</li>
<li><strong>Execute</strong> — files are written one at a time.</li>
<li><strong>Verify</strong> — lint/type checks run after every file, with a hard self-heal cap of 2 retries.</li>
<li><strong>Scan</strong> — Vanguard's pre-commit security patterns run automatically.</li>
<li><strong>Ship</strong> — nothing commits until you click Ship.</li>
</ul>
<h2>Pricing</h2>
<p>Free: 10 tasks/month, no credit card. Starter: $9/month flat — no per-token billing.</p>
<p><a href="/about">About AUREM</a> · <a href="/compare">How ORA compares</a> · <a href="/pricing">Pricing</a> · <a href="/signup">Start free</a></p>
</main>`;
}

function aboutBody() {
  const paras = ABOUT.paragraphs.map((p) => `<p>${esc(p)}</p>`).join("");
  const links = ABOUT.links.map((l) => `<a href="${esc(l.href)}">${esc(l.label)}</a>`).join(" · ");
  return `<main><h1>${esc(ABOUT.heading)}</h1>${paras}<h2>Links</h2><p>${links}</p></main>`;
}

function contactBody() {
  const paras = CONTACT.paragraphs.map((p) => `<p>${esc(p)}</p>`).join("");
  return `<main><h1>${esc(CONTACT.heading)}</h1>${paras}<p><a href="/support">Open a support ticket</a></p></main>`;
}

// 2026-09-12 · is-agentic.com agent-readiness fix — same no-JS
// server-rendered treatment for the new /developers portal.
function developersBody() {
  const intro = DEVELOPERS.intro.map((p) => `<p>${esc(p)}</p>`).join("");
  const sections = DEVELOPERS.sections.map((s) => {
    const links = s.links
      .map((l) => `<li><a href="${esc(l.href)}">${esc(l.label)}</a></li>`)
      .join("");
    return `<h2>${esc(s.heading)}</h2><p>${esc(s.body)}</p><ul>${links}</ul>`;
  }).join("");
  return `<main><h1>${esc(DEVELOPERS.heading)}</h1>${intro}${sections}` +
    `<p><a href="/about">About</a> · <a href="/contact">Contact</a> · <a href="/privacy">Privacy</a></p></main>`;
}

async function privacyBody(root) {
  // 2026-08-31 — dynamic import, NOT a static `import { marked }` at
  // module scope: this file is pulled into vite.config.js's own
  // dependency graph, which esbuild bundles to CJS at config-load
  // time. `marked` ships ESM-only (marked.esm.js) — a static import
  // gets rewritten to `require("marked")` there and crashes the prod
  // build with ERR_REQUIRE_ESM. Dynamic import() stays a real ESM
  // import even inside that bundled CJS wrapper.
  const { marked } = await import("marked");
  const md = readFileSync(join(root, "public", "policies", "privacy-policy.md"), "utf8");
  return `<main>${marked.parse(md)}</main>`;
}

const ROUTE_META = {
  "/": {
    title: "ORA by AUREM — the AI engineer that actually commits",
    description: "ORA connects to your GitHub repo, writes code, runs a security scan, and commits on your approval. Free tier: 10 tasks/month, no card. Starter: $9/month flat.",
    body: () => homeBody(),
  },
  "/about": {
    title: ABOUT.title, description: ABOUT.description, body: () => aboutBody(),
  },
  "/contact": {
    title: CONTACT.title, description: CONTACT.description, body: () => contactBody(),
  },
  "/privacy": {
    title: "Privacy Policy — AUREM",
    description: "AUREM's privacy policy: GDPR/PIPEDA/DPDP disclosures, cookie policy, data-retention windows, subprocessor list.",
    body: (root) => privacyBody(root),
  },
  "/developers": {
    title: DEVELOPERS.title, description: DEVELOPERS.description, body: () => developersBody(),
  },
};

export default function auremProdFixes() {
  let root = process.cwd();
  return {
    name: "aurem-prod-fixes",
    configResolved(cfg) { root = cfg.root; },
    configureServer(server) {
      // P0 #2 — real 404 for unknown top-level paths, BEFORE Vite's
      // own SPA fallback (which otherwise serves index.html for
      // literally everything, the exact soft-404 bug).
      server.middlewares.use((req, res, next) => {
        const urlPath = (req.url || "/").split("?")[0];
        if (
          urlPath === "/" ||
          urlPath.startsWith("/@") ||
          urlPath.startsWith("/src/") ||
          urlPath.startsWith("/node_modules/") ||
          urlPath.startsWith("/api/") ||
          // 2026-09-12 · is-agentic.com fix — real, extension-less
          // static files under public/ (RFC 8615 `.well-known/*`
          // directory: api-catalog, oauth-protected-resource,
          // http-message-signatures-directory) were getting shadowed
          // by this middleware and 404'd BEFORE Vite's own static
          // file server ever got a chance to serve them. Any path
          // that resolves to a real file on disk must always win.
          urlPath.startsWith("/.well-known/") ||
          existsSync(join(root, "public", urlPath)) ||
          HAS_EXTENSION.test(urlPath)
        ) {
          return next();
        }
        const first = urlPath.split("/")[1];
        if (KNOWN_PREFIXES.has(first)) return next();
        res.statusCode = 404;
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(
          "<!doctype html><meta name=\"robots\" content=\"noindex, follow\">" +
          "<title>404 · Page not found — AUREM</title>" +
          "<h1>Page not found</h1>" +
          "<p>This URL doesn't match any page on AUREM (the company " +
          "behind ORA, an autonomous AI software engineer). Either " +
          "the link is stale or the page moved. " +
          "<a href=\"/\">Back to auremcto.com</a></p>" +
          "<p>Looking for something specific? " +
          "<a href=\"/sitemap.xml\">sitemap.xml</a> · " +
          "<a href=\"/llms.txt\">llms.txt</a> · " +
          "<a href=\"/developers\">/developers</a> · " +
          "<a href=\"/api/docs\">/api/docs</a></p>",
        );
      });
    },
    // P0 #1 — real static content for non-JS crawlers on the routes
    // that matter most, injected into the SAME index.html Vite would
    // otherwise serve empty (#root) for every path.
    transformIndexHtml(html, ctx) {
      const urlPath = (ctx.originalUrl || ctx.path || "/").split("?")[0];
      const meta = ROUTE_META[urlPath];
      if (!meta) return html;
      return Promise.resolve(meta.body(root)).then((body) =>
        html
          .replace(/<title>[^<]*<\/title>/, `<title>${esc(meta.title)}</title>`)
          .replace(
            /<meta\s+name="description"\s+content="[^"]*"/,
            `<meta name="description" content="${esc(meta.description)}"`,
          )
          .replace(
            /<div id="root"><\/div>/,
            `<div id="root">${body}</div>`,
          ),
      );
    },
  };
}
