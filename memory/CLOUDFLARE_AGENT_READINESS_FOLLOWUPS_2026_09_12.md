# Cloudflare/Infra Follow-ups — is-agentic.com scan (2026-09-12)

These 2 items from the auremcto.com agent-readiness scan (65/100) are
**NOT fixable from application code** — they depend on Cloudflare
dashboard configuration (same category as `CLOUDFLARE_HEADERS_ITER393.md`,
which the founder already applied manually once before). Everything
else from the scan was fixed in code this round (see CHANGELOG.md,
2026-09-12 "Agent-readiness fixes" entry).

## 1. Markdown content negotiation (acceptmarkdown.com check)

**Scan finding**: `Accept: text/markdown` on the homepage returns
`text/html; charset=utf-8` with no `Vary: Accept` header.

**Why code can't fix this alone**: auremcto.com's frontend response
headers are set at the Cloudflare edge (per `CLOUDFLARE_HEADERS_ITER393.md`
— response headers for this domain are managed via Cloudflare
Transform Rules, not by the origin Vite dev server directly for
static/cached paths).

**What to do in Cloudflare dashboard** (Rules → Transform Rules → Modify Response Header):
- Add `Vary: Accept` to all responses (safe regardless of markdown
  support — it just tells caches the response can differ by Accept
  header).
- Optional, bigger lift: a Cloudflare Worker that, when it sees
  `Accept: text/markdown`, fetches the origin's normal HTML response
  and strips it down to a markdown-ish body (or proxies to a
  markdown-rendering endpoint). Not done here — flag for a future
  round if this specific check matters enough to prioritize.

## 2. Agent crawler reachability (ChatGPT-User / ClaudeBot)

**Scan finding**: `ChatGPT-User` and `ClaudeBot` user-agents show as
"unknown" (blocked or unreachable), while at least one other
bot-checking probe passed.

**Why code can't fix this alone**: this is almost certainly Cloudflare
Bot Fight Mode / Super Bot Fight Mode or a WAF managed rule
challenging/blocking these user-agents at the edge, before the
request ever reaches the origin pod. `robots.txt` on this app already
allows these agents (verified in `services/visibility` config) — the
block is happening upstream of the app.

**What to do in Cloudflare dashboard**:
- Security → Bots → check if "Bot Fight Mode" or "Super Bot Fight
  Mode" is ON. If so, add an explicit **Allow** rule (WAF → Custom
  Rules) for verified AI-crawler user-agents: `ChatGPT-User`,
  `ClaudeBot`, `GPTBot`, `PerplexityBot`, `Google-Extended`.
- Match condition example: `(http.user_agent contains "ChatGPT-User")
  or (http.user_agent contains "ClaudeBot")` → Action: **Skip** (bot
  fight mode) or **Allow**.

## Status
Everything else from the 65/100 scan (7 failures + partial credit on
6 warnings) was fixed in application code on 2026-09-12 — see
CHANGELOG.md for the full list. These 2 items are the only ones
requiring founder action outside this codebase.
