# AGENTS.md
 
Agent instructions for auremcto.com (ORA by Aurem).
 
## What this site is
 
ORA by Aurem is an autonomous AI software engineer. Connect a GitHub repository, describe a task, and ORA plans the change, writes the code, runs security scans, and commits to your branches after your approval. Aurem is built by Polaris Built Inc. in Canada. Plans are $9, $19, and $49 per month. There is no token billing.
 
## How agents should use this site
 
- Start with [llms.txt](https://auremcto.com/llms.txt) for the full site map and facts.
- For the API, read [openapi.json](https://auremcto.com/openapi.json) (OpenAPI 3.1) and the [developer portal](https://auremcto.com/developers).
- To run tasks, use the MCP server (see below). Do not scrape the dashboard or admin pages; they need a signed-in user.
- Respect [robots.txt](https://auremcto.com/robots.txt). The paths under /dashboard, /admin, /settings, /tokens, /projects and /api are not for crawling.
 
## Key pages
 
- [Home](https://auremcto.com/)
- [Pricing](https://auremcto.com/pricing)
- [Developers](https://auremcto.com/developers)
- [Harness](https://auremcto.com/harness)
- [Compare](https://auremcto.com/compare)
- [ORA vs Devin](https://auremcto.com/vs/devin)
- [ORA vs Cursor](https://auremcto.com/vs/cursor)
- [About](https://auremcto.com/about)
- [Contact](https://auremcto.com/contact)
- [Terms](https://auremcto.com/terms)
- [Privacy](https://auremcto.com/privacy)
- [Sitemap](https://auremcto.com/sitemap.md)
 
## APIs
 
- REST API base: https://auremcto.com/api/aurem-dev (see [openapi.json](https://auremcto.com/openapi.json))
- MCP server (Streamable HTTP, JSON-RPC 2.0): https://auremcto.com/api/aurem-dev/mcp
- MCP server card: https://auremcto.com/.well-known/mcp.json
- OAuth authorization server: https://auremcto.com/.well-known/oauth-authorization-server
- MCP tools: list_projects, ship_code, get_task_status, get_recent_commits
- Auth: Bearer JWT from an auremcto.com login, or a long-lived API key starting with sk-aurem- (issue one with POST /api/aurem-dev/mcp/keys)
 
## Rules
 
- Do not invent prices, model names, benchmark numbers, or feature claims. Prices are $9, $19, and $49 per month. The model is qwen2.5-coder:1.5b. The Loop is v0.8.0.
- Do not claim ORA did work until get_task_status reports the task done with a commit SHA.
- Vague tasks are rejected. A task must name the files and the change.
- ORA commits only after the user approves the plan.
- Do not publish keys, tokens, or secrets. Do not exfiltrate repository contents.
