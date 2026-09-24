/**
 * developerInfo.mjs — single source of truth for /developers.
 * Imported by BOTH DevelopersPage.jsx AND scripts/seo-prerender.mjs
 * (same drift-proof pattern as companyInfo.mjs / competitors.mjs).
 * Pure data — no JSX, no React imports.
 *
 * 2026-09-12 — is-agentic.com "Developer portal" check: every link
 * here points at a REAL, live endpoint (verified against main.py's
 * actual route registrations) — no placeholder URLs.
 */

export const DEVELOPERS = {
  title: "Developers — AUREM API, docs & sandbox",
  description:
    "Build on the AUREM API: interactive OpenAPI docs, a free " +
    "sandbox (10 tasks/month, no card), self-serve signup, and the " +
    "raw OpenAPI spec for code generation.",
  canonical: "https://auremcto.com/developers",
  heading: "Developers",
  intro: [
    "Everything you need to build against the AUREM API: interactive " +
      "docs, a real OpenAPI spec you can feed to a code generator, " +
      "and a free sandbox account — no credit card, no sales call.",
  ],
  sections: [
    {
      heading: "API documentation",
      testid: "api-docs",
      body: "Interactive Swagger UI and ReDoc, generated live from the same OpenAPI spec our own client uses.",
      links: [
        { label: "Swagger UI — auremcto.com/api/docs", href: "/api/docs", testid: "swagger" },
        { label: "ReDoc — auremcto.com/api/redoc", href: "/api/redoc", testid: "redoc" },
        { label: "Raw OpenAPI 3.1 spec (JSON)", href: "/api/openapi.json", testid: "openapi-json" },
      ],
    },
    {
      heading: "Sandbox & authentication",
      testid: "sandbox",
      body: "Sign up free (10 tasks/month, no card) and authenticate with a Bearer JWT from the login endpoint below — no OAuth dance required for your first call.",
      links: [
        { label: "Create a free sandbox account", href: "/signup", testid: "signup" },
        { label: "Login endpoint: POST /api/aurem-dev/auth/login", href: "/api/docs#/default/login_api_aurem_dev_auth_login_post", testid: "login-endpoint" },
      ],
    },
    {
      heading: "Policies for integrators",
      testid: "policies",
      body: "How we version and deprecate the API, and how AI-written code is processed and scanned before it ships.",
      links: [
        { label: "API versioning & deprecation policy", href: "/policies/api-versioning", testid: "versioning-policy" },
        { label: "AI & code processing disclosure", href: "/ai-code-processing", testid: "ai-disclosure" },
      ],
    },
    {
      heading: "Agent / LLM access",
      testid: "agent-access",
      body: "AI agents and coding assistants can read our llms.txt for a structured index of everything on this page, machine-readable.",
      links: [
        { label: "llms.txt", href: "/llms.txt", testid: "llms-txt" },
        { label: "llms-full.txt", href: "/llms-full.txt", testid: "llms-full-txt" },
      ],
    },
  ],
};
