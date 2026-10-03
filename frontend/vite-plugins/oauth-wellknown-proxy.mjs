/**
 * vite-plugins/oauth-wellknown-proxy.mjs — 2026-09-30 OAuth 2.1
 * discovery for the shared OAuth server (routers/oauth.py), covering
 * BOTH the general MCP "Claude Directory" listing and the admin-only
 * ORA Chat MCP server.
 *
 * WHY this exists: RFC 8414/9728 clients (Claude's MCP connector
 * included) look for these JSON docs at the BARE domain root, e.g.
 *   https://auremcto.com/.well-known/oauth-authorization-server
 * never under our internal /api/aurem-dev prefix. In preview, Vite's
 * dev-server proxy only forwards `/api/*` to the backend (see the
 * `proxy: { '/api': ... }` block in vite.config.js), so a bare
 * `/.well-known/*` request would otherwise fall through to the SPA
 * and get index.html back instead of JSON — exactly the bug real
 * production hit (frontend/Dockerfile's nginx has the equivalent fix
 * for THAT serving path; this is preview's).
 *
 * Rather than duplicating the JSON shape in JS, this just forwards
 * the request to the backend's already-`/api`-prefixed copy of the
 * SAME endpoint (routers/oauth.py mounts all three under
 * /api/aurem-dev too) and pipes the response straight back — any
 * future change to the metadata logic in Python is automatically
 * reflected here with zero JS changes needed.
 */
const PROXIED_PATHS = new Set([
  "/.well-known/oauth-authorization-server",
  "/.well-known/oauth-protected-resource",
  "/.well-known/openid-configuration",
]);

const BACKEND_INTERNAL_URL = "http://localhost:8001";

export default function oauthWellKnownProxy() {
  return {
    name: "oauth-wellknown-proxy",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const urlPath = (req.url || "/").split("?")[0];
        if (!PROXIED_PATHS.has(urlPath)) return next();
        try {
          const upstream = await fetch(`${BACKEND_INTERNAL_URL}/api/aurem-dev${urlPath}`);
          const body = await upstream.text();
          res.statusCode = upstream.status;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.setHeader("Cache-Control", "no-store");
          res.end(body);
        } catch (err) {
          res.statusCode = 502;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({ error: "well_known_proxy_failed", detail: String(err) }));
        }
      });
    },
  };
}
