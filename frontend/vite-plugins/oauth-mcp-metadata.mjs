/**
 * vite-plugins/oauth-mcp-metadata.mjs — 2026-09-29 OAuth 2.1 discovery
 * for the ORA Chat MCP server.
 *
 * WHY a Vite middleware and not just files in public/: per the MCP
 * authorization spec (RFC 8414 path-insertion), a client discovering
 * metadata for an issuer WITH a path component (ours is
 * `{origin}/api/aurem-dev/mcp`) must look at
 *   {origin}/.well-known/oauth-authorization-server/api/aurem-dev/mcp
 * i.e. at the BARE domain root — a path that does NOT start with
 * `/api`, so Kubernetes ingress sends it to the FRONTEND (port 3000),
 * never to the backend (confirmed: "All backend API endpoints must be
 * prefixed with /api"). A static file would need to hardcode the
 * domain (wrong for preview vs auremcto.com); this middleware computes
 * the correct absolute URLs from the live request instead, exactly
 * like vite-plugins/aurem-prod-fixes.mjs already does for its own
 * dev-server middleware. Confirmed (per that plugin's own docstring)
 * that Emergent's production deploy runs this SAME `vite` dev-server
 * command, not a static `dist/` build — so this middleware is live in
 * both preview and production identically.
 *
 * The ACTUAL OAuth protocol endpoints (register/authorize/token) all
 * live under /api/aurem-dev/oauth/* (backend/routers/oauth_mcp.py) —
 * this plugin only serves the two discovery documents that must sit
 * at the bare root.
 *
 * Does NOT touch the pre-existing, unrelated
 * public/.well-known/oauth-protected-resource file (general AUREM
 * platform API resource declaration, different `resource` value).
 */
const AS_PATH = "/.well-known/oauth-authorization-server/api/aurem-dev/mcp";
const PRM_PATH = "/.well-known/oauth-protected-resource/api/aurem-dev/mcp/ora-chat";

function originFromReq(req) {
  const proto = req.headers["x-forwarded-proto"] || "https";
  const host = req.headers["x-forwarded-host"] || req.headers.host;
  return `${proto}://${host}`;
}

function sendJson(res, obj) {
  const body = JSON.stringify(obj, null, 2);
  res.statusCode = 200;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(body);
}

export default function oauthMcpMetadata() {
  return {
    name: "oauth-mcp-metadata",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const urlPath = (req.url || "/").split("?")[0];
        if (urlPath !== AS_PATH && urlPath !== PRM_PATH) return next();
        const origin = originFromReq(req);
        if (urlPath === AS_PATH) {
          return sendJson(res, {
            issuer: `${origin}/api/aurem-dev/mcp`,
            authorization_endpoint: `${origin}/api/aurem-dev/oauth/authorize`,
            token_endpoint: `${origin}/api/aurem-dev/oauth/token`,
            registration_endpoint: `${origin}/api/aurem-dev/oauth/register`,
            response_types_supported: ["code"],
            grant_types_supported: ["authorization_code", "refresh_token"],
            token_endpoint_auth_methods_supported: ["none", "client_secret_basic"],
            code_challenge_methods_supported: ["S256"],
            scopes_supported: ["mcp"],
            authorization_response_iss_parameter_supported: true,
          });
        }
        return sendJson(res, {
          resource: `${origin}/api/aurem-dev/mcp/ora-chat`,
          authorization_servers: [`${origin}/api/aurem-dev/mcp`],
          bearer_methods_supported: ["header"],
          scopes_supported: ["mcp"],
          resource_name: "ORA Chat MCP (admin only)",
          resource_documentation: `${origin}/api/aurem-dev/mcp/ora-chat`,
        });
      });
    },
  };
}
