"""Router registry — L8 god-file split (2026-11-25).

Moved verbatim from backend/main.py (AuremHQ/Aurem main 097697ba):
  * the top-level router imports (main.py L23-83, L86-88, L95-141, plus
    the mcp_router half of the split L84 import and the oauth_router
    half of the split L89-94 import), and
  * the mount block (main.py L4116-4263: all 120 app.include_router calls,
    including the inline imports and the Iter 309 try/except SSE-probe
    mount), now inside register_routers(app).

Pure move — no behaviour change: same routers, same order, same prefixes
(ask_router is mounted twice, exactly as main.py did). main.py keeps one
call, register_routers(app), at the exact spot the mount block stood.

The three imports that stayed in main.py bind names main.py itself uses
outside mounting: routers.mcp.mcp_discovery_root, routers.api_catalog.
api_catalog_root, and the three routers.oauth *_root callables
(/.well-known add_route aliases).
"""

from routers.architect import router as architect_router
from routers.deploy import router as deploy_router
from routers.vault import router as vault_router
from routers.stacks import router as stacks_router
from routers.domain import router as domain_router
from routers.github_bot import router as github_router
from routers.harden import router as harden_router
from routers.trust import router as trust_router
from routers.chat_commits import router as chat_commits_router
from routers.engagement import router as engagement_router
from routers.unlock import router as unlock_router
from routers.auth import router as auth_router
from routers.mfa import router as mfa_router  # Iter 212m-20 — admin 2FA
from routers.chat import router as chat_router
from routers.dev_tools import router as dev_tools_router  # Iter 388t · Bug 20 · founder /podshell
from routers.github_oauth import router as github_oauth_router
from routers.google_oauth import router as google_oauth_router
from routers.github_funnel import router as github_funnel_router  # 2026-08-01 CTA drop-off telemetry
from routers.github_app import router as github_app_router  # 2026-02-10 · Phase 2 · install router + webhook
from routers.team_management import router as team_management_router  # 2026-02 · Phase 1 · team RBAC
from routers.cto_projects import router as cto_projects_router
from routers.automations import router as automations_router
from routers.upload import router as upload_router
from routers.migrations_admin import router as migrations_admin_router  # Feb 2026 — admin-gated migration framework endpoints (see backend/migrations/README.md)
from routers.backups_admin import router as backups_admin_router  # 2026-02-09 — admin-gated R2 backup endpoints (item #5)
from routers.admin_public import router as admin_public_router  # Iter 358 — public /admin/errors/report sink
from routers.admin_qa import router as admin_qa_router          # Iter 303 (/admin/qa dashboard)
from routers.admin_health import router as admin_health_router  # Feb 2026 — unified health registry aggregator
from routers.maintenance_public import router as maintenance_public_router  # 2026-08 — public maintenance status probe
from routers.admin_maintenance import router as admin_maintenance_router    # 2026-08 — admin maintenance toggle + outage tracker
from routers.admin_observability import router as admin_observability_router  # 2026-02-11 — /admin/observability/breakers
from routers.admin_payments import router as admin_payments_router  # 2026-02-11 — Phase 2 split: /admin/payments, /financials, /stripe-*, /billing/*
from routers.admin_support import router as admin_support_router  # 2026-02-11 — Phase 2 split: /admin/support, /errors, /alerts
from routers.admin_users import router as admin_users_router  # 2026-02-11 — Phase 2 split: /admin/users, /funnel, /insights
from routers.admin_funnel import router as admin_funnel_router  # 2026-09-28 MASTER TASK — GET /admin/funnel activation dashboard
from routers.admin_pricing import router as admin_pricing_router  # Master Prompt v2 PART 3 — margin editor, fixed costs, dashboard, billing-flags review
from routers.admin_rea import router as admin_rea_router  # 2026-10 — admin-only "/reverse-engineer <url>" ORA chat skill (github.com/morluto/rea)
from routers.admin_projects_brain import router as admin_projects_brain_router  # 2026-02-11 — Phase 2 split: /admin/projects, /project-brain, /architecture
from routers.admin_ops_config import router as admin_ops_config_router  # 2026-02-11 — Phase 2 split: /admin/cache, /feature-flags, /integrations, /settings, /github-app-config
from routers.admin_llm_config import router as admin_llm_config_router  # 2026-08-27 — Admin self-serve LLM Settings
from routers.admin_ora_chat_limits import router as admin_ora_chat_limits_router  # 2026-09-10 — editable daily token cap / rate limit
from routers.admin_video_pipeline import router as admin_video_pipeline_router  # 2026-09-10 — ORA Chat video generation (Deepgram + MuseTalk)
from routers.admin_llm_usd_cap import router as admin_llm_usd_cap_router  # 2026-08-28 — R6 USD cap
from routers.notifications_bell import router as notifications_bell_router  # 2026-08-28 — P2-A user-facing bell
from routers.admin_analytics import router as admin_analytics_router  # 2026-02-11 — Phase 2 split: /admin/dashboard, /audit, /loop-metrics, /council, /skills
from routers.admin_bi import router as admin_bi_router  # Slice A · BI Cockpit — /admin/bi/{stripe,inference,summary}
from routers.admin_health_score import router as admin_health_score_router  # 2026-08-23 — Codebase Health Score widget
from routers.admin_deploy_readiness import router as admin_deploy_readiness_router  # 2026-08-24 — Deploy Readiness card (Option A)
from routers.admin_context_bridge import router as admin_context_bridge_router  # 2026-09-09 — Chrome extension → ORA chat context bridge
from routers.founder_summary import router as founder_summary_router  # 2026-08-24 — Pillar 6, founder-language translation layer
from routers.rollback_v2 import router as rollback_v2_router  # 2026-06 — Pillar 1, snapshot-based two-phase rollback + drill
from routers.github_auth_migration import router as github_auth_migration_router  # 2026-06 — PAT→App migration
from routers.support import router as support_router
from routers.payments import router as payments_router
from routers.stripe_webhook_compat import router as stripe_webhook_compat_router  # Iter 388-ae — legacy /api/stripe/webhook alias (see Payments $0 root-cause memo)
from routers.harness import router as harness_router
from routers.deploy_share import router as deploy_share_router  # 2026-11-21 — Aurem Deploy Phase 2 · POST /api/deploy/share
from routers.ask import router as ask_router  # NLWeb: GET/POST /ask
from routers.harness_downloads import router as harness_downloads_router  # Aurem Loop harness Phase B — user-facing download
from routers.admin_harness import router as admin_harness_router  # Aurem Loop harness Phase B — admin release/download-log view
from routers.harness_telemetry import router as harness_telemetry_router  # Aurem Loop harness Fix E — /api/harness/telemetry (external client contract, no /aurem-dev segment)
from routers.mcp import router as mcp_router  # iter 173 — MCP server (split from main.py L84; mcp_discovery_root stays in main.py)
from routers.mcp_ora_chat import router as mcp_ora_chat_router  # 2026-09-28 ORA Chat MCP (admin-only, Part A)
from routers.admin_gateway import router as admin_gateway_router  # 2026-09-28 ORA Chat MCP/Gateway key admin API
from routers.vercel import router as vercel_router  # Iter 212m-84 — Vercel platform tools for ORA
from routers.oauth import router as oauth_router  # iter 182 — OAuth 2.1 + PKCE (split from main.py L89-94; *_root names stay in main.py)
from routers.usage import router as usage_router
from routers.lint_preview import router as lint_preview_router
from routers.shipwall import router as shipwall_router
from routers.wrapped import router as wrapped_router
from routers.hosted_deploy import router as hosted_deploy_router
from routers.github_deploy import router as github_deploy_router   # iter 123
from routers.thinking_hints import router as thinking_hints_router  # iter 158
from routers.repo_indexing import router as repo_indexing_router    # Iter 212m-30 PR-2
from routers.founder_offer import router as founder_offer_router    # Iter 212m-30 PR-2
from routers.onboarding_first_scan import router as onboarding_first_scan_router  # Onboarding Step 4 · S-B
from routers.onboarding import router as onboarding_router          # Iter 212m-32 nudge emails
from routers.auth_verify import router as auth_verify_router        # 2026-09-26 — email verification (was routers/promo_first50.py; promo removed, 0 users had ever claimed it)
from routers.email_unsubscribe import router as email_unsubscribe_router  # 2026-02-12 · CAN-SPAM/GDPR one-click unsubscribe
from routers.admin_vanguard import router as admin_vanguard_router  # Iter 212m-42 vanguard admin toggle
from routers.admin_bin import router as admin_bin_router  # Iter 212m-171 admin panel rebuild
from routers.admin_team import router as admin_team_router  # Admin Team S3 (AT2) — Team screen + invites
from routers.team_invite_accept import router as team_invite_accept_router  # Admin Team S3 (AT2) — invite accept
from routers.security_scan import router as security_scan_router    # Iter 212m-55 1-click vuln scanner
from routers.vanguard_ci import router as vanguard_ci_router        # Iter 212m-120 Phase 1: trufflehog CI ingest
from routers.synthetic_checks_ci import router as synthetic_checks_ci_router  # 2026-08-20 · G1/G15 CI ingest
from routers.fix_pipeline import router as fix_pipeline_router      # Iter 212m-121 bulk + SSE fix pipeline
from routers.project_versions import router as project_versions_router  # Part 2c B3 — Project Versions "Changes" page
from routers.repo_status import router as repo_status_router        # Iter 212m-125 live repo connection ping
from routers.codebase_health import router as codebase_health_router # Iter 212m-72 5-category health scanner
from routers.evidence import router as evidence_router  # 2026-09-20 — pointer+preview resolver (scan / loop_run)
from routers.verification import router as verification_router  # Trust & Verification round
from routers.scaffold import router as scaffold_router                # Iter 212m-231 Personal Track blank-slate
from routers.visual_bugfix import router as visual_bugfix_router      # 2026-09-12 Multimodal Visual Bug-Fix Phase 1+2
from routers.managed_db import router as managed_db_router            # Iter 212m-233 Personal Track shared DB
from routers.supabase   import router as supabase_router              # Iter 212m-234 Personal Track paid-tier Supabase
from routers.personal_track import router as personal_track_router    # Iter 212m-237 Personal Track activation events
from routers.ora_chat import router as ora_chat_router                # Iter 212m-238 Admin ORA Chat
from routers.findings      import router as findings_router         # Iter 212m-190 backlog + dismiss/snooze
from routers.suggestions   import router as suggestions_router      # Iter 212m-193 founder suggestion box
from routers.self_bugs_admin import router as self_bugs_admin_router  # Item 3 (2026-08-31) self-repair dashboard
from routers.admin_connect_diagnostic import router as admin_connect_diagnostic_router  # Connect-flow investigation (2026-09-01)
from routers.admin_onboarding_funnel import router as admin_onboarding_funnel_router  # Onboarding Funnel dashboard (2026-09-08)
from routers.version       import router as version_router          # Iter 212m-205 build info + system health
from routers.advisor_context import router as advisor_ctx_router     # Iter 212m-209 advisor read layer
from routers.project_memory import router as project_memory_router   # ORA Brain Part 2 — view/wipe own project memory
from routers.brain_admin import admin_router as brain_admin_router   # ORA Brain Part 3/4 — scoreboard, export, health, delete-own-data (admin)
from routers.brain_admin import user_router as brain_user_router     # ORA Brain Part 4, F3 — DELETE /api/brain/my-data (canonical path)
from routers.qa_probe      import router as qa_probe_router         # Iter 212m-190 QA-only chat-probe
from routers.loop          import router as loop_router             # Iter 212m-60 Loop Mode engine
from routers.diagram       import router as diagram_router          # Iter 212m-61 /diagram
from routers.feature_window import router as feature_window_router  # Iter 212m-64 system map
from services.codebase_indexer import router as codebase_router


def register_routers(app):
    """Mount every router on ``app`` — the 120 include_router calls moved
    verbatim from backend/main.py (L4116-4263 at main 097697ba).

    Same routers, same order, same prefixes. ``ask_router`` is mounted
    twice, exactly as before (NLWeb GET/POST /ask, no prefix).
    """
    # ── Routers ──
    app.include_router(deploy_router,       prefix="/api/aurem-dev")
    app.include_router(architect_router,    prefix="/api/aurem-dev")  # 2026-09-13 — Phase 4, "What I set up" panel
    app.include_router(vault_router,        prefix="/api/aurem-dev")
    app.include_router(stacks_router,       prefix="/api/aurem-dev")
    app.include_router(domain_router,       prefix="/api/aurem-dev")
    app.include_router(github_router,       prefix="/api/aurem-dev")
    app.include_router(harden_router,       prefix="/api/aurem-dev")
    app.include_router(trust_router,        prefix="/api/aurem-dev")
    app.include_router(chat_commits_router, prefix="/api/aurem-dev")
    app.include_router(engagement_router,   prefix="/api/aurem-dev")
    app.include_router(unlock_router,       prefix="/api/aurem-dev")
    app.include_router(auth_router,         prefix="/api/aurem-dev")
    app.include_router(mfa_router,          prefix="/api/aurem-dev")  # Iter 212m-20 — admin 2FA
    app.include_router(chat_router,         prefix="/api/aurem-dev")
    app.include_router(dev_tools_router,    prefix="/api/aurem-dev")  # Iter 388t · Bug 20 · founder /podshell
    app.include_router(github_oauth_router, prefix="/api/aurem-dev")
    app.include_router(google_oauth_router, prefix="/api/aurem-dev")
    app.include_router(github_funnel_router, prefix="/api/aurem-dev")  # 2026-08-01 CTA drop-off telemetry
    app.include_router(github_app_router, prefix="/api/aurem-dev")  # 2026-02-10 · Phase 2 · install router + webhook
    app.include_router(cto_projects_router, prefix="/api/aurem-dev")
    app.include_router(team_management_router, prefix="/api/aurem-dev")  # 2026-02 · Phase 1 · team RBAC
    app.include_router(automations_router, prefix="/api/aurem-dev")
    app.include_router(upload_router,        prefix="/api/aurem-dev")
    app.include_router(migrations_admin_router, prefix="/api/aurem-dev")  # /api/aurem-dev/admin/migrations/*
    app.include_router(backups_admin_router,    prefix="/api/aurem-dev")  # /api/aurem-dev/admin/backups/*
    app.include_router(admin_public_router,  prefix="/api/aurem-dev")  # Iter 358 — un-gated /admin/errors/report
    app.include_router(admin_qa_router,      prefix="/api/aurem-dev")  # Iter 303 — /admin/qa dashboard
    app.include_router(admin_health_router,  prefix="/api")             # Feb 2026 — /api/aurem-dev/admin/status/*
    app.include_router(maintenance_public_router, prefix="/api")        # 2026-08 — /api/aurem-dev/maintenance/status
    app.include_router(admin_maintenance_router,  prefix="/api")         # 2026-08 — /api/aurem-dev/admin/maintenance/*
    app.include_router(synthetic_checks_ci_router, prefix="/api/aurem-dev")  # 2026-08-20 · G1/G15 CI ingest
    app.include_router(admin_observability_router, prefix="/api")       # 2026-02-11 — /api/aurem-dev/admin/observability/breakers
    app.include_router(admin_payments_router, prefix="/api/aurem-dev")  # 2026-02-11 — Phase 2 split
    app.include_router(admin_support_router,  prefix="/api/aurem-dev")  # 2026-02-11 — Phase 2 split
    app.include_router(admin_users_router,          prefix="/api/aurem-dev")  # 2026-02-11 — Phase 2 split
    app.include_router(admin_funnel_router,         prefix="/api/aurem-dev")  # 2026-09-28 MASTER TASK — GET /admin/funnel
    app.include_router(admin_pricing_router,        prefix="/api/aurem-dev")  # Master Prompt v2 PART 3 — admin pricing panel
    app.include_router(admin_rea_router,            prefix="/api/aurem-dev")  # 2026-10 — admin "reverse engineer this website" chat skill
    app.include_router(admin_projects_brain_router, prefix="/api/aurem-dev")  # 2026-02-11 — Phase 2 split
    app.include_router(admin_ops_config_router,     prefix="/api/aurem-dev")  # 2026-02-11 — Phase 2 split
    app.include_router(admin_llm_config_router,     prefix="/api/aurem-dev")  # 2026-08-27 — Admin self-serve LLM Settings
    app.include_router(admin_llm_usd_cap_router,    prefix="/api/aurem-dev")  # 2026-08-28 — R6 USD cap
    app.include_router(admin_ora_chat_limits_router, prefix="/api/aurem-dev")  # 2026-09-10 — editable ORA Chat token cap / rate limit
    app.include_router(admin_video_pipeline_router, prefix="/api/aurem-dev")  # 2026-09-10 — ORA Chat video generation (Deepgram + MuseTalk)
    app.include_router(notifications_bell_router,   prefix="/api/aurem-dev")  # 2026-08-28 — P2-A user-facing bell
    app.include_router(admin_analytics_router,      prefix="/api/aurem-dev")  # 2026-02-11 — Phase 2 split
    app.include_router(admin_bi_router,             prefix="/api/aurem-dev")  # Slice A · BI Cockpit
    app.include_router(admin_health_score_router,   prefix="/api/aurem-dev")  # 2026-08-23 — Codebase Health Score
    app.include_router(admin_deploy_readiness_router, prefix="/api/aurem-dev")  # 2026-08-24 — Deploy Readiness (Option A)
    app.include_router(admin_context_bridge_router, prefix="/api/aurem-dev")  # 2026-09-09 — /admin/ora/context-bridge (Chrome extension)
    app.include_router(founder_summary_router,      prefix="/api/aurem-dev")  # 2026-08-24 — Pillar 6
    app.include_router(rollback_v2_router,          prefix="/api/aurem-dev")  # 2026-06 — Pillar 1
    app.include_router(github_auth_migration_router, prefix="/api/aurem-dev")  # 2026-06 — PAT→App migration
    app.include_router(support_router,       prefix="/api/aurem-dev")
    app.include_router(payments_router,      prefix="/api/aurem-dev")
    # Iter 388-ae · legacy Stripe dashboard path compatibility. Prod logs
    # on 2026-02-14 showed `POST /api/stripe/webhook 404` repeated for
    # every real payment — Stripe's endpoint had been configured at that
    # URL. This alias forwards to the canonical handler in payments.py so
    # no dashboard change is required.
    app.include_router(stripe_webhook_compat_router, prefix="/api")
    app.include_router(harness_router, prefix="/api")
    app.include_router(deploy_share_router, prefix="/api")  # 2026-11-21 — Aurem Deploy Phase 2 · POST /api/deploy/share (deployer -> ORA Chat inbox)
    app.include_router(ask_router)  # NLWeb: GET/POST /ask (no prefix)
    app.include_router(ask_router, prefix='/api')  # NLWeb: GET/POST /api/ask - same handler under /api so nginx (which only proxies /api/*) reaches it
    app.include_router(harness_downloads_router, prefix="/api/aurem-dev")  # /api/aurem-dev/harness/download — logged-in browser user
    app.include_router(admin_harness_router, prefix="/api/aurem-dev")  # /api/aurem-dev/admin/harness/* — release + download-log admin view
    app.include_router(harness_telemetry_router, prefix="/api")  # /api/harness/telemetry — external harness client, matches its hardcoded URL exactly
    app.include_router(usage_router,         prefix="/api/aurem-dev")
    app.include_router(lint_preview_router,  prefix="/api/aurem-dev")
    app.include_router(shipwall_router,      prefix="/api/aurem-dev")
    app.include_router(wrapped_router,       prefix="/api/aurem-dev")
    app.include_router(hosted_deploy_router, prefix="/api/aurem-dev")
    app.include_router(codebase_router,      prefix="/api/aurem-dev")
    app.include_router(scaffold_router,      prefix="/api/aurem-dev")  # Iter 212m-231
    app.include_router(visual_bugfix_router, prefix="/api/aurem-dev")  # 2026-09-12
    app.include_router(managed_db_router,    prefix="/api/aurem-dev")  # Iter 212m-233
    app.include_router(supabase_router,      prefix="/api/aurem-dev")  # Iter 212m-234 Phase 5 paid-tier
    app.include_router(personal_track_router, prefix="/api/aurem-dev")  # Iter 212m-237 activation events
    app.include_router(ora_chat_router,       prefix="/api/aurem-dev")  # Iter 212m-238 admin ORA Chat
    app.include_router(github_deploy_router, prefix="/api/aurem-dev")   # iter 123
    app.include_router(thinking_hints_router, prefix="/api/aurem-dev")  # iter 158
    app.include_router(repo_indexing_router,  prefix="/api/aurem-dev")  # Iter 212m-30 PR-2
    app.include_router(founder_offer_router,  prefix="/api/aurem-dev")  # Iter 212m-30 PR-2
    app.include_router(onboarding_first_scan_router, prefix="/api/aurem-dev")  # Onboarding Step 4 · S-B
    app.include_router(onboarding_router,     prefix="/api/aurem-dev")  # Iter 212m-32 nudge emails
    app.include_router(auth_verify_router,    prefix="/api/aurem-dev")  # 2026-09-26 — email verification (was promo_first50_router)
    app.include_router(email_unsubscribe_router,        prefix="/api/aurem-dev")  # 2026-02-12 · one-click unsubscribe
    app.include_router(admin_vanguard_router, prefix="/api/aurem-dev")  # Iter 212m-42 vanguard config
    app.include_router(admin_bin_router,      prefix="/api/aurem-dev")  # Iter 212m-171 admin panel rebuild
    app.include_router(admin_team_router,         prefix="/api/aurem-dev")  # Admin Team S3 (AT2)
    app.include_router(team_invite_accept_router, prefix="/api/aurem-dev")  # Admin Team S3 (AT2)
    app.include_router(security_scan_router,  prefix="/api/aurem-dev")  # Iter 212m-55 1-click vuln scanner
    app.include_router(vanguard_ci_router,    prefix="/api/aurem-dev")  # Iter 212m-120 Phase 1: trufflehog CI ingest
    app.include_router(fix_pipeline_router,   prefix="/api/aurem-dev")  # Iter 212m-121 bulk + SSE fix pipeline
    app.include_router(project_versions_router, prefix="/api/aurem-dev")  # Part 2c B3 — Project Versions "Changes" page
    app.include_router(repo_status_router,    prefix="/api/aurem-dev")  # Iter 212m-125 live repo connection ping
    app.include_router(codebase_health_router, prefix="/api/aurem-dev")  # Iter 212m-72 5-cat health
    app.include_router(evidence_router, prefix="/api/aurem-dev")  # 2026-09-20 — evidence pointer resolver
    app.include_router(verification_router, prefix="/api/aurem-dev")  # Trust & Verification round
    app.include_router(findings_router,        prefix="/api/aurem-dev")  # Iter 212m-190 backlog + dismiss
    app.include_router(suggestions_router,     prefix="/api/aurem-dev")  # Iter 212m-193 founder suggestions
    app.include_router(self_bugs_admin_router, prefix="/api/aurem-dev")  # Item 3 (2026-08-31) self-repair dashboard
    app.include_router(admin_connect_diagnostic_router, prefix="/api/aurem-dev")  # Connect-flow investigation (2026-09-01)
    app.include_router(admin_onboarding_funnel_router, prefix="/api/aurem-dev")  # Onboarding Funnel dashboard (2026-09-08)
    app.include_router(version_router)                                  # Iter 212m-205 (already prefixed inside)
    app.include_router(advisor_ctx_router)                              # Iter 212m-209 (already prefixed inside)
    app.include_router(project_memory_router)                           # ORA Brain Part 2 (already prefixed inside)
    app.include_router(brain_admin_router)                              # ORA Brain Part 3 (already prefixed inside)
    app.include_router(brain_user_router)                               # ORA Brain Part 4, F3 (already prefixed inside)
    app.include_router(qa_probe_router,        prefix="/api/aurem-dev")  # Iter 212m-190 QA-only probe
    # Iter 212m-158 — /tools page "Notify me" capture endpoint.
    from routers.notify_interest import router as notify_interest_router
    app.include_router(notify_interest_router, prefix="/api/aurem-dev")

    # Iter 273 — public integrity log for /both landing page
    from routers.integrity_log import router as integrity_log_router
    app.include_router(integrity_log_router, prefix="/api/aurem-dev")
    app.include_router(loop_router,           prefix="/api/aurem-dev")  # Iter 212m-60 Loop Mode engine
    # Iter 366 — user-facing rollback (Pro/Team, non-admin)
    from routers.user_rollback import router as user_rollback_router
    app.include_router(user_rollback_router,  prefix="/api/aurem-dev")

    # ── Iter 309 · SSE 25-min reconnect validation harness ──────────────
    # TEST-ONLY router. Refuses all requests with 404 unless the server
    # was started with AUREM_ENABLE_SSE_PROBE=1 in the environment. Safe
    # to leave included in prod as long as the env var is off.
    try:
        from routers.dev_sse_probe import router as _iter309_probe_router
        app.include_router(_iter309_probe_router, prefix="/api")
    except Exception as _e:  # noqa: BLE001
        import logging as _log
        _log.getLogger(__name__).warning(
            "[iter309-probe] router not mounted: %r", _e,
        )
    # Iter 212m-117 — User trust-level (L1/L2/L3) for Loop Mode + Fix.
    from routers.trust_level import router as trust_level_router
    app.include_router(trust_level_router)
    app.include_router(diagram_router,        prefix="/api/aurem-dev")  # Iter 212m-61 /diagram
    app.include_router(feature_window_router, prefix="/api/aurem-dev")  # Iter 212m-64 system map
    app.include_router(mcp_router,            prefix="/api/aurem-dev")  # iter 173 — MCP server
    app.include_router(mcp_ora_chat_router,   prefix="/api/aurem-dev")  # 2026-09-28 ORA Chat MCP (admin-only)
    app.include_router(admin_gateway_router,  prefix="/api/aurem-dev")  # 2026-09-28 ORA Chat MCP/Gateway key admin API
    app.include_router(vercel_router,         prefix="/api/aurem-dev")  # Iter 212m-84 — Vercel tools for ORA
    app.include_router(oauth_router,          prefix="/api/aurem-dev")  # iter 182 — OAuth 2.1 + PKCE for Claude Directory / ORA Chat MCP
    from routers.visibility import router as visibility_router          # Phase 3 · Visibility Kit Phase B (2026-08-28)
    app.include_router(visibility_router,     prefix="/api/aurem-dev")
