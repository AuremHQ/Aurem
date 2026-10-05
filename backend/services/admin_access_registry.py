"""Admin Team S1 (AT1a) — the route registry.

Maps every admin-gated route to (screen_key, level). This is the single
source of truth the widened gate (`cto_services/auth.py`) reads from the
request's MATCHED route at runtime — no route handler is edited to look
itself up; FastAPI/Starlette expose the matched route's path template via
`request.scope["route"].path`, and the gate does `REGISTRY.get((method, path))`.

Built from:
  - /app/docs/admin-map/PART-2-screen-keys-and-levels.md (~140 routes)
  - The Q7 corrected-total gap list (ora_chat.py, mfa.py, codebase_health.py,
    dev_tools.py, github_bot.py, security_scan.py, verification.py,
    admin_analytics.py's 5 `/admin/skills/*` routes — corrected from "admin.py"
    to their real home, admin_analytics.py, after tracing router mounts;
    `routers/admin.py`'s own APIRouter is never `include_router`'d — its
    helper functions are imported live elsewhere, but its OWN decorated
    routes are unreachable dead code and need no registry entry)
  - R1 (default: GET=View, any other method=owner-only) for anything not
    explicitly decided
  - R2 (key assignments for the 38 extra routes)
  - R3 (dev_tools /podshell = owner-only, no key)
  - D16 (GET on paysetup/githubapp/video/models = View; every WRITE on
    those four = owner-only, superseding PART-2's earlier "Manage" flags)
  - D5/D17 (ora.ask and all 7 ask-ORA actions = owner-only)

Levels: 0=off (unused here — a missing entry means "not yet decided",
not "off"), 1=View, 2=Work, 3=Manage, 0 is reserved for OWNER_ONLY below
as a sentinel distinct from the four real levels.
"""
from __future__ import annotations

VIEW = 1
WORK = 2
MANAGE = 3
OWNER_ONLY = "owner_only"   # never satisfied by any role level — only Owner

# (method, path_template, screen_key, level)
# path_template is the FULL path as Starlette's matched route exposes it,
# i.e. WITH the "/api/aurem-dev" (or "/ora-chat") app-level prefix.
ADMIN_ROUTE_REGISTRY: list[tuple[str, str, str, object]] = [
    # ───────────────────────── home.home ─────────────────────────
    ("GET", "/api/aurem-dev/admin/dashboard", "home.home", VIEW),
    ("GET", "/api/aurem-dev/admin/presence/summary", "home.home", VIEW),
    ("GET", "/api/aurem-dev/admin/pulse", "home.home", VIEW),
    ("GET", "/api/aurem-dev/admin/bi/summary", "home.home", VIEW),
    ("GET", "/api/aurem-dev/admin/alerts", "home.home", VIEW),

    # ───────────────────────── users.all / plans / github ─────────
    ("GET", "/api/aurem-dev/admin/users", "users.all", VIEW),
    ("GET", "/api/aurem-dev/admin/users/{user_id}", "users.all", VIEW),
    ("GET", "/api/aurem-dev/admin/users/{user_id}/chat-sessions", "users.all", VIEW),
    ("GET", "/api/aurem-dev/admin/chat-sessions/{session_id}", "users.all", VIEW),
    ("POST", "/api/aurem-dev/admin/users/{user_id}/enable-loop-beta", "users.all", WORK),
    ("POST", "/api/aurem-dev/admin/users/{user_id}/suspend", "users.all", MANAGE),
    ("DELETE", "/api/aurem-dev/admin/users/{user_id}", "users.all", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/users/{user_id}/grant-tokens", "users.all", MANAGE),
    ("POST", "/api/aurem-dev/admin/users/email-offer", "users.all", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/users/notify", "users.all", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/funnel", "users.all", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/activation-funnel", "growth.activation", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/activation-funnel/stage-users", "growth.activation", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/first-message-sample", "growth.insights", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/user-patterns", "growth.insights", VIEW),
    ("POST", "/api/aurem-dev/admin/dev-users/backfill-created-at", "users.all", MANAGE),
    ("GET", "/api/aurem-dev/admin/dev-users/created-at-health", "work.idea", VIEW),
    ("POST", "/api/aurem-dev/admin/loop-beta/kill-switch", "settings.switches", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/loop-beta/status", "settings.switches", VIEW),
    ("GET", "/api/aurem-dev/admin/bin/{bin_id}/projects", "users.plans", VIEW),
    ("POST", "/api/aurem-dev/admin/users/{bin_id}/tier", "users.plans", MANAGE),
    ("GET", "/api/aurem-dev/admin/github/connections", "users.github", VIEW),
    ("GET", "/api/aurem-dev/admin/github/stuck-connections", "users.github", VIEW),
    ("POST", "/api/aurem-dev/admin/github/bulk-revoke", "users.github", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/github/clear-stuck-state", "users.github", WORK),
    ("POST", "/api/aurem-dev/admin/github/flag-idle", "users.github", WORK),
    ("POST", "/api/aurem-dev/admin/github-app/repair-orphaned-installations", "users.github", MANAGE),
    ("GET", "/api/aurem-dev/github/status", "users.github", VIEW),
    ("POST", "/api/aurem-dev/github/push", "users.github", OWNER_ONLY),

    # ───────────────────────── work.* ──────────────────────────────
    ("GET", "/api/aurem-dev/admin/projects", "work.projects", VIEW),
    ("GET", "/api/aurem-dev/admin/tasks", "work.tasks", VIEW),
    ("GET", "/api/aurem-dev/admin/architecture", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/admin/code-surface", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/admin/eval-quality", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/admin/architecture-health", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/admin/duplication", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/admin/churn-risk", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/admin/brain/{project_id}/dump", "work.idea", VIEW),
    ("GET", "/api/aurem-dev/admin/brain/{project_id}/recent-commits", "work.idea", VIEW),
    ("POST", "/api/aurem-dev/admin/brain/{project_id}/replay", "work.idea", WORK),
    ("DELETE", "/api/aurem-dev/admin/project-brain/{project_id}/decision", "work.idea", OWNER_ONLY),
    ("DELETE", "/api/aurem-dev/admin/project-brain/{project_id}/preference", "work.idea", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/project-brain/{project_id}/decision", "work.idea", WORK),
    ("POST", "/api/aurem-dev/admin/project-brain/{project_id}/preference", "work.idea", WORK),
    ("GET", "/api/aurem-dev/admin/loop-inspect/{loop_id}", "work.tasks", VIEW),
    ("GET", "/api/aurem-dev/admin/speed-diagnostic", "work.slow", VIEW),
    ("GET", "/api/aurem-dev/admin/scope-drift-audit", "work.scope", VIEW),
    ("GET", "/api/aurem-dev/admin/visibility-kit/dashboard", "work.visibility", VIEW),
    ("GET", "/api/aurem-dev/admin/postscan-issues", "ora.reliability", VIEW),
    # scaffold.py (prefix "/scaffold") — Personal Track Admin
    ("POST", "/api/aurem-dev/scaffold/{draft_id}/founder-override", "work.idea", OWNER_ONLY),
    ("GET", "/api/aurem-dev/scaffold/admin/llm-health", "work.idea", VIEW),
    ("GET", "/api/aurem-dev/scaffold/admin/blocked-drafts", "work.idea", VIEW),
    ("GET", "/api/aurem-dev/scaffold/admin/personal-projects", "work.idea", VIEW),
    ("GET", "/api/aurem-dev/scaffold/admin/draft-summary", "work.idea", VIEW),
    ("POST", "/api/aurem-dev/scaffold/admin/smoke-test", "work.idea", WORK),
    ("GET", "/api/aurem-dev/scaffold/admin/revenue-snapshot", "work.idea", VIEW),
    # supabase.py (prefix "/supabase") — same screen
    ("GET", "/api/aurem-dev/supabase/admin/pending-downgrades", "work.idea", VIEW),
    ("POST", "/api/aurem-dev/supabase/admin/sweep-now", "work.idea", MANAGE),
    ("POST", "/api/aurem-dev/supabase/admin/rearm/{app_id}", "work.idea", WORK),

    # ───────────────────────── support.* ───────────────────────────
    ("GET", "/api/aurem-dev/admin/support", "support.tickets", VIEW),
    ("POST", "/api/aurem-dev/admin/support/{ticket_id}/reply", "support.tickets", WORK),
    ("POST", "/api/aurem-dev/admin/support/{ticket_id}/resolve", "support.tickets", MANAGE),
    ("GET", "/api/aurem-dev/suggestions/admin/list", "support.suggestions", VIEW),
    ("POST", "/api/aurem-dev/suggestions/admin/{suggestion_id}/decide", "support.suggestions", MANAGE),

    # ───────────────────────── money.* ─────────────────────────────
    ("GET", "/api/aurem-dev/admin/financials", "money.summary", VIEW),
    ("GET", "/api/aurem-dev/admin/payments", "money.payments", VIEW),
    ("POST", "/api/aurem-dev/admin/payments/reconcile", "money.payments", MANAGE),
    ("POST", "/api/aurem-dev/admin/financials/settings", "money.summary", MANAGE),
    ("GET", "/api/aurem-dev/admin/token-pnl", "money.pnl", VIEW),
    ("GET", "/api/aurem-dev/admin/llm-credits", "money.credits", VIEW),
    ("POST", "/api/aurem-dev/admin/llm-credit-alert", "money.credits", WORK),
    ("GET", "/api/aurem-dev/admin/llm/rate-table", "money.caps", VIEW),
    ("GET", "/api/aurem-dev/admin/llm/usd-caps", "money.caps", VIEW),
    ("POST", "/api/aurem-dev/admin/llm/usd-caps", "money.caps", MANAGE),
    ("GET", "/api/aurem-dev/admin/llm/spend/{user_id}", "money.caps", VIEW),
    ("POST", "/api/aurem-dev/admin/llm/backfill", "money.caps", MANAGE),

    # ───────────────────────── growth.* ────────────────────────────
    ("GET", "/api/aurem-dev/admin/onboarding-funnel/summary", "growth.onboarding", VIEW),
    ("GET", "/api/aurem-dev/admin/onboarding-funnel/trend", "growth.onboarding", VIEW),
    ("GET", "/api/aurem-dev/admin/onboarding-funnel/users", "growth.onboarding", VIEW),
    ("GET", "/api/aurem-dev/admin/onboarding-funnel/preview-email", "growth.onboarding", VIEW),
    ("POST", "/api/aurem-dev/admin/onboarding-funnel/winback/send-batch", "growth.winback", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/onboarding-funnel/casl-screen", "growth.onboarding", VIEW),
    ("GET", "/api/aurem-dev/admin/onboarding-funnel/winback/preview", "growth.onboarding", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/dora", "growth.insights", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/slo", "growth.insights", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/cost-alert", "growth.insights", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/journey-watch", "growth.insights", VIEW),
    ("GET", "/api/aurem-dev/admin/insights/confidence-checks", "growth.insights", VIEW),

    # ───────────────────────── ora.* ───────────────────────────────
    ("GET", "/api/aurem-dev/admin/agent-performance", "ora.performance", VIEW),
    ("GET", "/api/aurem-dev/admin/ora/stats", "ora.performance", VIEW),
    ("GET", "/api/aurem-dev/admin/ora-stats", "ora.performance", VIEW),
    ("POST", "/api/aurem-dev/admin/ora/export", "ora.performance", VIEW),
    ("GET", "/api/aurem-dev/admin/failure-patterns", "ora.failures", VIEW),
    ("POST", "/api/aurem-dev/admin/failure-patterns/mark-fixed", "ora.failures", WORK),
    ("GET", "/api/aurem-dev/admin/warm-start-stats", "ora.reliability", VIEW),
    ("GET", "/api/aurem-dev/admin/vanguard/recent", "ora.scanner", VIEW),
    ("GET", "/api/aurem-dev/admin/vanguard/stats", "ora.scanner", VIEW),
    ("GET", "/api/aurem-dev/admin/vanguard/config", "ora.scanner", VIEW),
    ("POST", "/api/aurem-dev/admin/vanguard/config", "ora.scanner", MANAGE),
    ("POST", "/api/aurem-dev/security-scan/run", "ora.scanner", MANAGE),
    ("GET", "/api/aurem-dev/admin/preview-scan", "ora.scanner", VIEW),
    ("GET", "/api/aurem-dev/admin/council/health", "ora.council", VIEW),
    ("GET", "/api/aurem-dev/admin/council-health", "ora.council", VIEW),
    ("POST", "/api/aurem-dev/admin/council/reprobe", "ora.council", WORK),
    ("GET", "/api/aurem-dev/admin/parliament/live", "ora.council", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/latest-report", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard20-incidents", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/fabrication-patterns", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/regression-patterns", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/codebase-health/cache-stats", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/codebase-health/scanner-feedback", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/codebase-health/telemetry/bug-hunt-triggers", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/admin/trust-calibration", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/ora-learning/weekly-summary", "ora.learning", VIEW),
    ("GET", "/api/aurem-dev/admin/digest", "ora.learning", VIEW),
    ("POST", "/api/aurem-dev/admin/skills/web-search", "ora.learning", WORK),
    ("POST", "/api/aurem-dev/admin/skills/fetch-url", "ora.learning", WORK),
    ("POST", "/api/aurem-dev/admin/skills/search-and-summarize", "ora.learning", WORK),
    ("POST", "/api/aurem-dev/admin/skills/firecrawl-scrape", "ora.learning", WORK),
    ("POST", "/api/aurem-dev/admin/skills/firecrawl-crawl", "ora.learning", WORK),
    ("GET", "/api/aurem-dev/admin/house-rules", "ora.instructions", VIEW),
    ("PUT", "/api/aurem-dev/admin/house-rules", "ora.instructions", MANAGE),
    ("GET", "/api/aurem-dev/admin/robot-guide", "ora.instructions", VIEW),
    ("PUT", "/api/aurem-dev/admin/robot-guide", "ora.instructions", MANAGE),
    ("GET", "/api/aurem-dev/admin/self-bugs/list", "ora.bugs", VIEW),
    ("POST", "/api/aurem-dev/admin/self-bugs/mark-fixed", "ora.bugs", WORK),
    ("POST", "/api/aurem-dev/admin/self-bugs/review-proposal", "ora.bugs", WORK),

    # ora_chat.py (prefix "/ora-chat") — D5/D17/R2: ora.ask is owner-only,
    # including the 7 catalog actions, the dedicated house-rules/
    # hallucination-pattern/canary/preview-scan sub-groups per R2.
    ("GET", "/api/aurem-dev/ora-chat/sessions", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/sessions", "ora.ask", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/sessions/{session_id}", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/action/approve", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/action/reject", "ora.ask", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/actions/recent", "ora.ask", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/usage", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/slash", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/message", "ora.ask", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/config", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/config", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/intent-classify", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/upload", "ora.ask", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/image-generate", "ora.ask", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/image-status", "ora.ask", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/house-rules", "ora.instructions", OWNER_ONLY),
    ("PUT", "/api/aurem-dev/ora-chat/house-rules", "ora.instructions", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/house-rules/history", "ora.instructions", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/house-rules/restore/{version}", "ora.instructions", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/house-rules/reset", "ora.instructions", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/hallucination-patterns", "ora.failures", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/hallucination-patterns/classify-now", "ora.failures", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/hallucination-patterns/approve", "ora.failures", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/hallucination-patterns/reject", "ora.failures", OWNER_ONLY),
    ("POST", "/api/aurem-dev/ora-chat/canary/run-now", "ora.reliability", OWNER_ONLY),
    ("GET", "/api/aurem-dev/ora-chat/canary/runs", "ora.reliability", OWNER_ONLY),
    # pin settings are identity, not a screen key — see PART-2 settings.* note
    ("GET", "/api/aurem-dev/ora-chat/pin/status", None, VIEW),
    ("POST", "/api/aurem-dev/ora-chat/pin/set", None, WORK),

    # ───────────────────────── system.* ────────────────────────────
    ("GET", "/api/aurem-dev/admin/status/all", "system.status", VIEW),
    ("POST", "/api/aurem-dev/admin/status/{check_id}/ack", "system.status", WORK),
    ("GET", "/api/aurem-dev/admin/status/notifications", "logs.notifications", VIEW),
    ("POST", "/api/aurem-dev/admin/status/notifications/mark-read", "logs.notifications", VIEW),
    ("POST", "/api/aurem-dev/admin/status/notifications/mark-read/{notif_id}", "logs.notifications", VIEW),
    ("GET", "/api/aurem-dev/admin/deploy-readiness", "system.status", VIEW),
    ("GET", "/api/aurem-dev/admin/integrations/health", "system.services", VIEW),
    ("POST", "/api/aurem-dev/admin/integrations/refresh", "system.services", WORK),
    ("POST", "/api/aurem-dev/admin/cache/purge", "system.services", WORK),
    ("GET", "/api/aurem-dev/admin/cache/stats", "system.services", VIEW),
    ("GET", "/api/aurem-dev/admin/cache/analytics-stats", "system.services", VIEW),
    ("POST", "/api/aurem-dev/admin/cache/analytics-invalidate", "system.services", WORK),
    ("GET", "/api/aurem-dev/admin/observability/breakers", "system.services", VIEW),
    ("GET", "/api/aurem-dev/admin/observability/breakers/{dep}", "system.services", VIEW),
    ("GET", "/api/aurem-dev/admin/guardrails", "system.services", VIEW),
    ("POST", "/api/aurem-dev/admin/guardrails/{rule}/mode", "system.services", MANAGE),
    ("POST", "/api/aurem-dev/admin/sentry/test", "system.services", WORK),
    ("GET", "/api/aurem-dev/admin/backups/status", "system.backups", VIEW),
    ("GET", "/api/aurem-dev/admin/system-stats", "system.resources", VIEW),
    ("GET", "/api/aurem-dev/admin/health-score", "system.codehealth", VIEW),
    ("POST", "/api/aurem-dev/admin/health-score/test-coverage/run", "system.codehealth", WORK),
    ("GET", "/api/aurem-dev/admin/health-score/architecture-review", "system.codehealth", VIEW),
    ("POST", "/api/aurem-dev/admin/health-score/architecture-review", "system.codehealth", WORK),
    ("GET", "/api/aurem-dev/admin/db-health", "system.database", VIEW),
    ("GET", "/api/aurem-dev/admin/migrations/status", "system.database", VIEW),
    ("POST", "/api/aurem-dev/admin/migrations/apply", "system.database", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/migrations/verify", "system.database", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/maintenance", "system.maintenance", VIEW),
    ("POST", "/api/aurem-dev/admin/maintenance/settings", "system.maintenance", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/maintenance/incidents", "system.maintenance", VIEW),
    # dev_tools.py — R3: no key, owner-only, never grantable to any role
    ("POST", "/api/aurem-dev/dev-tools/podshell", None, OWNER_ONLY),
    ("GET", "/api/aurem-dev/dev-tools/podshell/info", None, OWNER_ONLY),

    # ───────────────────────── settings.* ──────────────────────────
    ("GET", "/api/aurem-dev/admin/stripe-prices", "settings.plans", VIEW),
    ("POST", "/api/aurem-dev/admin/stripe-prices", "settings.plans", MANAGE),
    # D16 + D4: GET=View, every write=owner-only, on all 4 secret-holding screens
    ("GET", "/api/aurem-dev/admin/stripe-config", "settings.paysetup", VIEW),
    ("POST", "/api/aurem-dev/admin/stripe-config", "settings.paysetup", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/llm/configs", "settings.models", VIEW),
    ("POST", "/api/aurem-dev/admin/llm/configs", "settings.models", OWNER_ONLY),
    ("PUT", "/api/aurem-dev/admin/llm/configs/{config_id}", "settings.models", OWNER_ONLY),
    ("DELETE", "/api/aurem-dev/admin/llm/configs/{config_id}", "settings.models", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/llm/configs/{config_id}/set-active", "settings.models", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/llm/configs/{config_id}/test", "settings.models", WORK),
    ("GET", "/api/aurem-dev/admin/github-app-config", "settings.githubapp", VIEW),
    ("POST", "/api/aurem-dev/admin/github-app-config", "settings.githubapp", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/github-app-diagnostics", "settings.githubapp", VIEW),
    ("GET", "/api/aurem-dev/admin/video/secrets", "settings.video", VIEW),
    ("PUT", "/api/aurem-dev/admin/video/secrets", "settings.video", OWNER_ONLY),
    ("DELETE", "/api/aurem-dev/admin/video/secrets", "settings.video", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/llm/ora-chat-limits", "settings.hints", VIEW),
    ("PUT", "/api/aurem-dev/admin/llm/ora-chat-limits", "settings.hints", MANAGE),
    ("GET", "/api/aurem-dev/admin/thinking-hints", "settings.hints", VIEW),
    ("POST", "/api/aurem-dev/admin/thinking-hints", "settings.hints", MANAGE),
    ("PUT", "/api/aurem-dev/admin/thinking-hints/{hint_id}", "settings.hints", MANAGE),
    ("DELETE", "/api/aurem-dev/admin/thinking-hints/{hint_id}", "settings.hints", MANAGE),
    ("GET", "/api/aurem-dev/admin/thinking-hints-config", "settings.hints", VIEW),
    ("POST", "/api/aurem-dev/admin/thinking-hints-config", "settings.hints", MANAGE),
    ("GET", "/api/aurem-dev/admin/feature-flags", "settings.switches", VIEW),
    ("POST", "/api/aurem-dev/admin/feature-flags/{flag}/toggle", "settings.switches", MANAGE),
    ("GET", "/api/aurem-dev/admin/gateway/keys", "settings.apikeys", VIEW),
    ("POST", "/api/aurem-dev/admin/gateway/keys", "settings.apikeys", WORK),
    ("POST", "/api/aurem-dev/admin/gateway/keys/{key_id}/revoke", "settings.apikeys", WORK),
    ("POST", "/api/aurem-dev/admin/gateway/keys/{key_id}/regenerate", "settings.apikeys", WORK),
    ("PATCH", "/api/aurem-dev/admin/gateway/keys/{key_id}/limits", "settings.apikeys", WORK),
    ("POST", "/api/aurem-dev/admin/gateway/test/mcp", "settings.apikeys", WORK),
    ("GET", "/api/aurem-dev/admin/gateway/oauth-clients", "settings.apikeys", VIEW),
    ("POST", "/api/aurem-dev/admin/gateway/oauth-clients/{client_id}/revoke", "settings.apikeys", MANAGE),
    ("GET", "/api/aurem-dev/admin/mcp-usage", "settings.usage", VIEW),
    # mfa.py — R4: not a screen key (None); own-account self-service.
    # status/enroll-start/enroll-verify work WITHOUT a 2FA session (R4);
    # disable requires one. Enforced at S2, registry only records the key.
    ("GET", "/api/aurem-dev/admin/2fa/status", None, VIEW),
    ("POST", "/api/aurem-dev/admin/2fa/enroll-start", None, WORK),
    ("POST", "/api/aurem-dev/admin/2fa/enroll-verify", None, WORK),
    ("POST", "/api/aurem-dev/admin/2fa/disable", None, MANAGE),

    # ───────────────────────── logs.* ──────────────────────────────
    ("GET", "/api/aurem-dev/admin/audit/actions", "logs.admin", VIEW),
    ("GET", "/api/aurem-dev/admin/audit", "logs.activity", VIEW),

    # ───────────────────────── connect-diagnostic (no dedicated screen) ──
    ("GET", "/api/aurem-dev/admin/connect-diagnostic/{user_id}", None, OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/connect-diagnostic/audit/stale-github-urls", None, OWNER_ONLY),

    # ───────────────────────── S1 fitness-test sweep additions ─────
    # (found by walking the real app.routes, not by manual file reading —
    # see STAGE-1.md for how these were discovered)
    ("GET", "/api/aurem-dev/admin/rollback2/attempts", "system.database", VIEW),
    ("GET", "/api/aurem-dev/admin/rollback2/drills", "system.database", VIEW),
    ("GET", "/api/aurem-dev/admin/rollback2/snapshots", "system.database", VIEW),
    ("POST", "/api/aurem-dev/admin/rollback2/drill", "system.database", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/rollback2/execute", "system.database", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/rollback2/preview", "system.database", WORK),
    ("POST", "/api/aurem-dev/admin/rollback2/snapshot", "system.database", WORK),
    ("GET", "/api/aurem-dev/admin/qa/guard1-route-sweep", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard3-scope-drift", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard5-invariants", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard6-dedup-indexes", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard7-payment-recon", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard10-founder-alerts", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard12-rollback", "ora.quality", VIEW),
    ("POST", "/api/aurem-dev/admin/qa/guard12-rollback/trigger", "ora.quality", WORK),
    ("GET", "/api/aurem-dev/admin/qa/guard13-cost", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard14-signup-abuse", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard15-deps", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard16-auth-hardening", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard17-breakers", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard18-timeout-audit", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard19-recovery", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/guard21-security-scan", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/status", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/counts", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/browser-selftest", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/ci-vs-local-drift", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/risk-routing/summary", "ora.quality", VIEW),
    ("GET", "/api/aurem-dev/admin/qa/vscode-marketplace-status", "ora.quality", VIEW),
    ("POST", "/api/aurem-dev/admin/qa/cleanup-e2e-sessions", "ora.quality", WORK),
    ("POST", "/api/aurem-dev/admin/qa/correction-rules/graduate", "ora.quality", WORK),
    ("GET", "/api/aurem-dev/admin/video/avatars", "settings.video", VIEW),
    ("POST", "/api/aurem-dev/admin/video/avatars", "settings.video", MANAGE),
    ("DELETE", "/api/aurem-dev/admin/video/avatars/{avatar_id}", "settings.video", MANAGE),
    ("GET", "/api/aurem-dev/admin/video/house-rules", "settings.video", VIEW),
    ("PUT", "/api/aurem-dev/admin/video/house-rules", "settings.video", MANAGE),
    ("GET", "/api/aurem-dev/admin/video/jobs", "settings.video", VIEW),
    ("GET", "/api/aurem-dev/admin/video/jobs/{job_id}", "settings.video", VIEW),
    ("POST", "/api/aurem-dev/admin/video/jobs", "settings.video", WORK),
    ("DELETE", "/api/aurem-dev/admin/video/secrets/fal-key", "settings.video", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/backups/list", "system.backups", VIEW),
    ("GET", "/api/aurem-dev/admin/backups/drill-history", "system.backups", VIEW),
    ("POST", "/api/aurem-dev/admin/backups/run", "system.backups", WORK),
    ("POST", "/api/aurem-dev/admin/backups/drill-now", "system.backups", WORK),
    ("POST", "/api/aurem-dev/admin/backups/test-restore", "system.backups", WORK),
    ("GET", "/api/aurem-dev/admin/errors", "ora.bugs", VIEW),
    ("POST", "/api/aurem-dev/admin/errors/{error_id}/autofix", "ora.bugs", WORK),
    ("POST", "/api/aurem-dev/admin/errors/{error_id}/autofix/apply", "ora.bugs", WORK),
    ("POST", "/api/aurem-dev/admin/errors/{error_id}/autofix/reject", "ora.bugs", WORK),
    ("POST", "/api/aurem-dev/admin/errors/{error_id}/resolve", "ora.bugs", WORK),
    ("POST", "/api/aurem-dev/admin/feature-flags", "settings.switches", MANAGE),
    ("POST", "/api/aurem-dev/admin/feature-flags/{flag}/user-override", "settings.switches", MANAGE),
    ("DELETE", "/api/aurem-dev/admin/feature-flags/{flag}/user-override/{bin_id}", "settings.switches", MANAGE),
    ("GET", "/api/aurem-dev/admin/github-auth/pat-inventory", "users.github", VIEW),
    ("POST", "/api/aurem-dev/admin/github-auth/migrate", "users.github", OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/github/installations/unlinked", "users.github", VIEW),
    ("POST", "/api/aurem-dev/admin/github/installations/reconcile", "users.github", MANAGE),
    ("POST", "/api/aurem-dev/admin/github/installations/{installation_id}/link", "users.github", WORK),
    ("GET", "/api/aurem-dev/admin/github-sync", "users.github", VIEW),
    ("GET", "/api/aurem-dev/admin/github-webhook-fence", "users.github", VIEW),
    ("GET", "/api/aurem-dev/admin/drift-alerts", "users.github", VIEW),
    ("GET", "/api/aurem-dev/admin/live-model-mode", "settings.models", VIEW),
    ("GET", "/api/aurem-dev/admin/mode-telemetry", "ora.performance", VIEW),
    ("GET", "/api/aurem-dev/admin/council/stats", "ora.council", VIEW),
    ("GET", "/api/aurem-dev/admin/funnel/activation", "growth.activation", VIEW),
    ("GET", "/api/aurem-dev/admin/bi/inference-metrics", "home.home", VIEW),
    ("GET", "/api/aurem-dev/admin/bi/stripe-metrics", "home.home", VIEW),
    ("GET", "/api/aurem-dev/admin/agent-tokens", "settings.apikeys", VIEW),
    ("GET", "/api/aurem-dev/admin/boundary-probes", "ora.reliability", VIEW),
    ("GET", "/api/aurem-dev/admin/debug/repo_context_timings", "system.resources", VIEW),
    ("GET", "/api/aurem-dev/admin/founder-summary/{event_id}", "home.home", VIEW),
    ("POST", "/api/aurem-dev/admin/founder-summary/generate", "home.home", WORK),
    ("GET", "/api/aurem-dev/admin/graph-status", "system.codehealth", VIEW),
    ("GET", "/api/aurem-dev/admin/learning-health", "ora.learning", VIEW),
    ("GET", "/api/aurem-dev/admin/loop-metrics", "ora.performance", VIEW),
    ("GET", "/api/aurem-dev/admin/loop-token-metrics", "ora.performance", VIEW),
    ("GET", "/api/aurem-dev/admin/me", None, VIEW),
    ("GET", "/api/aurem-dev/admin/overview-metrics", "home.home", VIEW),
    ("GET", "/api/aurem-dev/admin/preview-deploy-monitor", "system.status", VIEW),
    ("GET", "/api/aurem-dev/admin/product-analytics", "growth.insights", VIEW),
    ("GET", "/api/aurem-dev/admin/project-brain/{project_id}", "work.idea", VIEW),
    ("GET", "/api/aurem-dev/admin/project-brain/{project_id}/trigger-audit", "work.idea", VIEW),
    ("GET", "/api/aurem-dev/admin/settings", None, VIEW),
    ("POST", "/api/aurem-dev/admin/settings", None, OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/skills-usage", "ora.learning", VIEW),
    ("GET", "/api/aurem-dev/admin/skills/status", "ora.learning", VIEW),
    ("GET", "/api/aurem-dev/admin/synthetic-checks/heartbeat", "system.status", VIEW),
    ("POST", "/api/aurem-dev/admin/billing/run-overage-cron", "money.summary", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/dev/kill-supervised-task/{name}", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/dev/clear-supervised-postmortem/{name}", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/migrations/down", "system.database", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/migrations/up", "system.database", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/migrations/mark-applied/{version}", "system.database", OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/llm/rate-table", "money.caps", MANAGE),
    ("POST", "/api/aurem-dev/admin/llm/usd-caps/backfill", "money.caps", MANAGE),
    ("GET", "/api/aurem-dev/admin/llm/usd-caps/spend/{user_id}", "money.caps", VIEW),
    ("POST", "/api/aurem-dev/admin/ora/context-bridge", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/seo/run", "growth.insights", WORK),
    ("POST", "/api/aurem-dev/admin/status/notifications/{notif_id}/mark-read", "logs.notifications", VIEW),
    ("POST", "/api/aurem-dev/admin/alerts/{alert_id}/dismiss", "home.home", WORK),
    ("POST", "/api/aurem-dev/ora-chat/preview-scan", "ora.scanner", WORK),

    # Admin Team S3 (AT2) — Team screen + invite management is itself
    # owner-only (no team member manages the team, including their own
    # role/overrides) — every route here is `screen_key=None`, which the
    # gate treats the same as OWNER_ONLY (D9). `/team-invite/accept` is
    # NOT here deliberately: it isn't admin-gated at all (any logged-in
    # user with a valid invite token can call it).
    ("GET", "/api/aurem-dev/admin/team/members", None, OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/team/invites", None, OWNER_ONLY),
    ("GET", "/api/aurem-dev/admin/team/screens", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/team/invites", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/team/invites/{invite_id}/copy-link", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/team/invites/{invite_id}/cancel", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/team/members/{user_id}/suspend", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/team/members/{user_id}/resume", None, OWNER_ONLY),
    ("POST", "/api/aurem-dev/admin/team/members/{user_id}/remove", None, OWNER_ONLY),
    ("PATCH", "/api/aurem-dev/admin/team/members/{user_id}/overrides", None, OWNER_ONLY),
]

import re as _re

_PARAM_RE = _re.compile(r"\{[^{}]+\}")


def _normalize_path(path: str) -> str:
    """Collapse any `{param_name}` segment to `{*}`. The registry is
    hand-written and the real route's path-param NAME (e.g. `{bin_id}`
    vs `{user_id}`) is an implementation detail of whichever handler
    owns that route — normalizing both sides before lookup means a
    registry entry typo'd with the wrong param name fails LOUDLY (no
    match at all, not a wrong-but-silent one) instead of this exact
    class of bug (found live: `{user_id}` written for the real
    `{bin_id}` on POST /admin/users/{bin_id}/tier) silently 403-ing a
    team member who should have had access."""
    return _PARAM_RE.sub("{*}", path)


_REGISTRY_MAP: dict[tuple[str, str], tuple] = {
    (method, _normalize_path(path)): (screen_key, level)
    for method, path, screen_key, level in ADMIN_ROUTE_REGISTRY
}


def lookup_route(method: str, path: str):
    """Returns (screen_key, level) or None if this exact (method, path)
    isn't in the registry. D9/D19 — the gate treats None (not found) the
    SAME as an explicit OWNER_ONLY entry: unregistered context is
    owner-only, never silently open to a team member."""
    return _REGISTRY_MAP.get((method, _normalize_path(path)))


# Admin Team S2 (AT1b) / R4 — mfa.py's 4 self-service routes manage the
# CALLING member's own 2FA and are therefore accessible to any active
# team member regardless of their screen/role (hence `screen_key=None`
# above), WITHOUT requiring the mfa-claim already being set for the 3
# routes that bootstrap 2FA in the first place — `disable` is the
# exception, since lifting 2FA protection is itself sensitive enough to
# require an already-MFA-verified session (D6).
MFA_SELF_SERVICE: dict[tuple[str, str], dict] = {
    ("GET", "/api/aurem-dev/admin/2fa/status"):        {"require_mfa_claim": False},
    ("POST", "/api/aurem-dev/admin/2fa/enroll-start"):  {"require_mfa_claim": False},
    ("POST", "/api/aurem-dev/admin/2fa/enroll-verify"): {"require_mfa_claim": False},
    ("POST", "/api/aurem-dev/admin/2fa/disable"):       {"require_mfa_claim": True},
}
