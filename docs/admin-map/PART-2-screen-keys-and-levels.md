# Admin Panel Map — Part 2: Screen-Key + Level Assignment (Turn A, answers only)
No code, .env, or config changed to produce this file. Built directly on top of
`PART-1.md` (same branch, same scan). Every row keeps PART-1's file:line citations.

Level legend: **V**=1 View, **W**=2 Work, **M**=3 Manage, **O**=owner-only (never
grantable to any role, per the prompt's explicit list).

## home.home
| Method | Path | File:Line | Old screen | Level | Reason (mutations only) |
|---|---|---|---|---|---|
| GET | /admin/dashboard | admin_analytics.py:96 | Cockpit, Dashboard (rows 2,5) | V | — |
| GET | /admin/presence/summary | admin_users.py:97 | Cockpit (row 2) | V | — |
| GET | /admin/pulse | admin_analytics.py:133 | Cockpit (row 2) | V | — |
| GET | /admin/bi/summary | admin_bi.py:407 | Live BI (row 3) | V | — |
| GET | /admin/alerts | admin_support.py:167 | Overview (row 6) | V | — |
| POST | /admin/alerts/{id}/dismiss¹ | admin_support.py (near :167)¹ | Overview (row 6) | W | dismissing an alert is reversible — it reappears on next trigger |
¹ PART-1 only lists the GET for alerts; the "dismiss" action named in row 6's "Does" column has no path:line traced yet — **UNVERIFIED**, needs Part 3 drill-down.

**Composite-page note:** per the prompt's own rule ("Home only returns items from
keys the member can open"), Home/Overview/Cockpit is a shell that embeds widgets
owned by OTHER keys (backups, growth.insights, users.github, ora.council,
settings.switches, system.*, etc. — see those sections below). Each such widget
is tagged under its OWN key in this doc, not under `home.home`, so a member who
only holds `home` sees the shell + the two rows above and nothing else; a member
who also holds e.g. `system.backups` additionally sees that widget rendered
inside the same page. This mirrors the owner's stated model, but it means the
Cockpit/Overview/Dashboard **frontend components must filter which embedded
widget they mount per the viewer's own key set** — that UI work does not exist
today and is new build scope for AT3 (COMBINED PAGES rules), not AT1.

## users.all / users.plans / users.github
| Method | Path | File:Line | Old screen | New key | Level | Reason |
|---|---|---|---|---|---|---|
| GET | /admin/users | admin_users.py:123 | Users list (row 7) | users.all | V | — |
| GET | /admin/users/{id} | admin_users.py:274 | User drawer (row 8) | users.all | V | — |
| GET | /admin/users/{id}/chat-sessions | admin_users.py:1549 | User drawer (row 8) | users.all | V — gated additionally by **chats** switch (session count/titles today; no turn text) | — |
| GET | /admin/chat-sessions/{id} | admin_users.py:1569 | User drawer (row 8) | users.all | V — gated by **chats** switch (full turn text) | — |
| POST | /admin/users/{id}/enable-loop-beta | admin_users.py:615 | User drawer (row 8) | users.all | W | per-user flag flip, reversible |
| POST | /admin/users/{id}/suspend | admin_users.py:837 | Users list (row 7) | users.all | M | affects exactly one user, reversible (unsuspend) |
| DELETE | /admin/users/{id} | admin_users.py:855 | Users list (row 7) | users.all | **O** | explicit: "delete a user" |
| POST | /admin/users/{id}/grant-tokens | admin_users.py:559 | Users list (row 7) | users.all | M | affects one user, reversible by granting 0/negative adjustment |
| POST | /admin/users/email-offer | admin_users.py:909 | Users list (row 7) | users.all | **flag — see below** | accepts up to **500** `user_ids` in one call — not "one user" |
| POST | /admin/users/notify | admin_users.py:1059 | Users list (row 7) | users.all | **flag — see below** | same bulk shape as email-offer |
| GET | /admin/bin/{id}/projects | admin_bin.py:71 | BIN Tracker (row 9) | users.plans | V | — |
| POST | /admin/users/{id}/tier | admin_bin.py:158 | BIN Tracker (row 9) | users.plans | M | one user's tier |
| GET | /admin/github/connections | admin_bin.py:252 | Bulk Revoke (row 10) | users.github | V | — |
| GET | /admin/github/stuck-connections | admin_bin.py:667 | Bulk Revoke (row 10) | users.github | V | — |
| POST | /admin/github/bulk-revoke | admin_bin.py:396 | Bulk Revoke (row 10) | users.github | **O** | explicit: "bulk revoke" |
| POST | /admin/github/clear-stuck-state | admin_bin.py:756 | Bulk Revoke (row 10) | users.github | W | clears a stuck flag, reversible by re-sync |
| POST | /admin/github/flag-idle | admin_bin.py:571 | Bulk Revoke (row 10) | users.github | W | flags, doesn't delete |
| POST | /admin/github-app/repair-orphaned-installations | admin_users.py:1216 | no screen (#59) | users.github | M | repairs installs; `dry_run` default true limits blast radius |
| POST | /admin/dev-users/backfill-created-at | admin_users.py:1449 | no screen (#60) | users.all | M | one-time data-repair across many rows — **flag**, same bulk-shape issue as above |

**Open question (bulk actor, not "one user"):** the level ladder as given
(View → Work → Manage-affects-one-user) has no rung for "affects many users at
once but is non-destructive" — email-offer, notify, and the backfill/repair
endpoints above all fit this gap. I have **not** assigned them a final level;
flagging for your call in A8/before AT1: either (a) cap all of these at Manage
as a pragmatic reading of "Work + bigger", or (b) owner-only by the same
precedent as bulk-revoke. I'd lean (b) for email-offer/notify specifically
since they reach customer inboxes/notifications at scale, and (a) for the two
idempotent backfill/repair jobs since they're one-time maintenance, not
recurring member-facing actions — but this is a recommendation, not a default.

## work.tasks / work.projects / work.slow / work.scope / work.visibility / work.idea
| Method | Path | File:Line | Old screen | New key | Level | Reason |
|---|---|---|---|---|---|---|
| GET | /admin/projects | admin_projects_brain.py:53 | Projects (row 11) | work.projects | V | — |
| GET | /admin/tasks | admin_projects_brain.py:63 | Tasks (row 12) | work.tasks | V — gated by **code** switch (task body may include diff/code fields; exact field names UNVERIFIED, flagged for Part 3) | — |
| GET | /admin/architecture | admin_projects_brain.py:80 | Architecture (row 13) | system.codehealth¹ | V | — |
| GET | /admin/code-surface | admin_projects_brain.py:375 | Architecture (row 13) | system.codehealth¹ | V — gated by **code** switch | — |
| GET | /admin/eval-quality | admin_analytics.py:1052 | Architecture (row 13) | system.codehealth¹ | V | — |
| GET | /admin/architecture-health, /duplication, /churn-risk | admin_projects_brain.py:437,468,480 | no screen (#61) | system.codehealth¹ | V | — |
| GET | /admin/brain/{id}/dump | admin_projects_brain.py:321 | Brain Dump (row 14) | work.idea² | V — gated by **code** switch (decisions/preferences can contain code) | — |
| GET | /admin/brain/{id}/recent-commits | admin_projects_brain.py:495 | Brain Dump (row 14) | work.idea² | V — gated by **code** switch | — |
| POST | /admin/brain/{id}/replay | admin_projects_brain.py:535 | Brain Dump (row 14) | work.idea² | W | a read-style query against brain, doesn't mutate it |
| DELETE | /admin/project-brain/{id}/decision | admin_projects_brain.py:309 | Brain Dump (row 14) | work.idea² | **O** | explicit: "erase ORA memory" |
| DELETE | /admin/project-brain/{id}/preference | admin_projects_brain.py:599 | Brain Dump (row 14) | work.idea² | **O** | same — erases stored ORA memory |
| POST | /admin/project-brain/{id}/decision, /preference (ADD) | admin_projects_brain.py:281,297 | no screen (#62) | work.idea² | W | adds, doesn't erase; reversible by deleting the one just added (itself owner-only, which is an inconsistency worth a decision: see note) |
| GET | /admin/loop-inspect/{loop_id} | admin_analytics.py:2860 | Inspect Loop (row 15) | work.tasks | V | — |
| GET | /admin/speed-diagnostic | admin_analytics.py:2932 | Speed Diagnostic (row 16) | work.slow | V | — |
| GET | /admin/scope-drift-audit | admin_analytics.py:2947 | Scope Drift Audit (row 17) | work.scope | V | — |
| GET | /admin/visibility-kit/dashboard | admin_analytics.py:2522 | Visibility Kit (row 38) | work.visibility | V | — |
| GET | /admin/dev-users/created-at-health | admin_users.py:1510 | Personal Track Admin (row 42) | work.idea² | V | — |
| GET/POST `/scaffold/admin/*`, `/supabase/admin/*` | routers/scaffold.py + unopened supabase router | Personal Track Admin (row 42) | work.idea² | V/W — **UNVERIFIED**, backend file for the supabase-prefixed router not opened this turn (same gap PART-1 already flagged) | sweep/rearm actions read as reversible (W); smoke-test read-only-ish (W) |

¹ "Architecture" and "Health Score" are explicitly grouped under **one** key,
`system.codehealth`, by the SCREEN KEYS block — even though Architecture is
today a Projects/Tasks-area screen. I followed the prompt's literal key
definition over the old UI grouping.
² "Personal Track Admin" is explicitly named as `work.idea` in the SCREEN KEYS
block, and I folded Brain Dump into the same key since both are "stored
decisions/preferences about one project" in substance — **flagging this as an
assumption**: Brain Dump could just as easily be its own future key if you want
it split from Personal Track later; today's 56-key list has no separate slot
for it, so `work.idea` was the closest fit.

**Note on the asymmetry above:** ADD-decision/preference is Work while
DELETE-decision/preference is owner-only. That's an intentional reflection of
your "erase ORA memory" rule, not an oversight — flagging only so it isn't
mistaken for an inconsistency later.

## support.tickets / support.suggestions
| Method | Path | File:Line | Old screen | Level | Reason |
|---|---|---|---|---|---|
| GET | /admin/support | admin_support.py:51 | Support (row 32) | V | — |
| POST | /admin/support/{id}/reply | admin_support.py:90 | Support (row 32) | W | one ticket, fully reversible (can reply again) |
| POST | /admin/support/{id}/resolve | admin_support.py:150 | Support (row 32) | M | changes one ticket's terminal status — matches your example shape `{"$set": {"status": ..., "resolved_at": ...}}` |
| GET | /suggestions/admin/list | suggestions.py:260 | Suggestions (row 33) | V | — |
| POST | /suggestions/admin/{id}/decide | suggestions.py:292 | Suggestions (row 33) | M | decides one suggestion's fate |

## money.summary / money.payments / money.pnl / money.credits / money.caps
| Method | Path | File:Line | Old screen | New key | Level | Reason |
|---|---|---|---|---|---|---|
| GET | /admin/financials | admin_payments.py:173 | Cockpit + Financials (rows 2,23) | money.summary | V | — |
| GET | /admin/payments | admin_payments.py:58 | Financials (row 23) | money.payments | V | — |
| POST | /admin/payments/reconcile | admin_payments.py:101 | Financials (row 23) | money.payments | M | re-syncs Stripe status; touches many payment rows at once — same bulk-shape flag as users.all above, recommend Manage-ceiling here specifically since it's read-mostly reconciliation, not a user-facing send |
| POST | /admin/financials/settings | admin_payments.py:186 | Financials (row 23) | money.summary | M | platform-wide setting, not "one user" — same ladder gap noted once; treating as Manage pragmatically |
| GET | /admin/token-pnl | admin_analytics.py:527 | Token P&L (row 24) | money.pnl | V | — |
| GET | /admin/llm-credits | admin_bin.py:960 | LLM Credits (row 25) | money.credits | V | — |
| POST | /admin/llm-credit-alert | admin_bin.py:1053 | LLM Credits (row 25) | money.credits | W | sets a threshold, reversible |
| GET/POST | /admin/llm/rate-table, /admin/llm/usd-caps, /spend/{user_id}, /backfill | admin_llm_usd_cap.py:50,57,67,74,84,95 | no screen (#56) | money.caps | V (GET) / M (POST — platform cap, same ladder-gap note) | — |

## growth.activation / growth.onboarding / growth.winback / growth.insights
| Method | Path | File:Line | Old screen | New key | Level | Reason |
|---|---|---|---|---|---|---|
| GET | /admin/funnel/activation | admin_funnel.py:100 | Activation Funnel (row 40) | growth.activation | V | — |
| GET | /admin/insights/activation-funnel | admin_users.py:1118-1395 range | Overview (row 6) | growth.activation | V | same data family as above |
| GET | /admin/onboarding-funnel/summary, /trend, /users, /preview-email | admin_onboarding_funnel.py:83,190,242,120 | Onboarding Funnel (row 39) | growth.onboarding | V | — |
| POST | /admin/onboarding-funnel/winback/send-batch | admin_onboarding_funnel.py:321 | no screen (#50) | growth.winback | **flag — see note** | sends an email **batch** to many users — same bulk-shape gap; I'd lean **owner-only** here given it reaches customer inboxes, but flagging rather than asserting |
| GET | /casl-screen | admin_onboarding_funnel.py:217 | no screen (#50) | growth.onboarding | V | — |
| GET | /admin/insights/dora, /slo, /cost-alert, /journey-watch, /user-patterns | admin_users.py:1118-1395 range | Overview (row 6) | growth.insights | V | — |
| GET | /admin/insights/confidence-checks, /first-message-sample | admin_users.py:1194,1331 | no screen (#58) | growth.insights | V | confidence-checks query returns raw log rows incl. `response_confidence_log` text — flag for **chats**-switch review in AT3, same shape as a chat-text leak |

## ora.ask / performance / failures / reliability / scanner / council / quality / bugs / instructions / learning
| Method | Path | File:Line | Old screen | New key | Level | Reason |
|---|---|---|---|---|---|---|
| GET/POST/etc. `/ora-chat/*` (sessions, action, usage) | ora_chat.py | ORA Chat admin (row 41) | ora.ask | **see A4 — stays owner/admin only, not role-limited** | — |
| GET | /admin/agent-performance | admin_analytics.py:2133 | Agent Performance (row 18) | ora.performance | V | — |
| GET | /admin/failure-patterns | admin_analytics.py:1486 | Failure Patterns (row 20) | ora.failures | V | — |
| POST | /admin/failure-patterns/mark-fixed | admin_analytics.py:1733 | Failure Patterns (row 20) | ora.failures | W | marks fixed, reversible (unmark not found, but non-destructive) |
| GET | /admin/warm-start-stats | admin_analytics.py:2031 | Reliability (row 21) | ora.reliability | V | — |
| GET | /admin/postscan-issues | admin_projects_brain.py:608 | Reliability (row 21) | ora.reliability | V — gated by **code** switch | — |
| GET | /admin/vanguard/recent, /stats | admin_analytics.py:1384,1370 | Vanguard (row 22) | ora.scanner | V | — |
| GET/POST | /admin/vanguard/config | admin_vanguard.py:36,55 | Vanguard (row 22) | ora.scanner | M | changes scanner behaviour platform-wide — ladder-gap note applies |
| GET | /admin/council/health, /admin/council-health (alias) | admin_analytics.py:429,496 | System Health (row 30) / no-screen (#48) | ora.council | V | — |
| POST | /admin/council/reprobe | admin_analytics.py:467 | no screen (#48) | ora.council | W | re-runs a probe, non-destructive |
| GET | /admin/parliament/live | admin_bin.py:1081 | Parliament Live (row 37) | ora.council | V | — |
| GET | /admin/loop-beta/status | admin_users.py:679 | QA Dashboard (row 36) | settings.switches¹ | V | — |
| POST | /admin/loop-beta/kill-switch | admin_users.py:654 | QA Dashboard (row 36) | settings.switches¹ | **O** | explicit key label is "switches (Feature Flags, **kill switch**)" and a kill switch is the "emergency stop" class of action |
| GET | /admin/qa/latest-report, /guard20-incidents, /fabrication-patterns, /regression-patterns (+~18 more guardN-*) | admin_qa.py:144,246,268,295,… | QA Dashboard (row 36) | ora.quality | V | — |
| GET | /admin/qa/cleanup-e2e-sessions² | admin_analytics.py (row 2's file, ~line 61) | no screen (#48) | ora.quality | — | method unclear from PART-1 (listed as POST in #48 but path name reads like a cleanup job) — **UNVERIFIED**, flag M/O pending Part 3 |
| GET | /admin/ora-learning/weekly-summary | admin_analytics.py:800 | no screen (#48) | ora.learning | V | — |
| GET | /admin/digest | admin_analytics.py:951 | no screen (#48) | ora.learning | V | — |
| GET | /admin/ora/stats, /admin/ora-stats | admin_analytics.py:960,968 | no screen (#48) | ora.performance | V | — |
| POST | /admin/ora/export | admin_analytics.py:1018 | no screen (#48) | ora.performance | V (read+download, not a mutation) | gated by **export** switch |
| GET | /admin/self-bugs/list | self_bugs_admin.py:40 | Self Bugs (row 34) | ora.bugs | V | — |
| POST | /admin/self-bugs/mark-fixed, /review-proposal | self_bugs_admin.py:107,132 | Self Bugs (row 34) | ora.bugs | W | marks/reviews, non-destructive |
| GET/PUT | /admin/house-rules | admin_ops_config.py:1038,1054 | House Rules (row 46) | ora.instructions | V (GET) / M (PUT — platform-wide prompt change) | — |
| GET/PUT | /admin/robot-guide | admin_ops_config.py:1086,1105 | Robot Guide (row 47) | ora.instructions | V (GET) / M (PUT) | — |

¹ moved from the old "QA/Vanguard" grouping to `settings.switches` per the
SCREEN KEYS block's explicit wording — see the composite-page note below.
² distinguish from the similarly-named `/admin/qa/*` guard-report reads above,
which are a different file/feature.

**Composite-page note (QA Dashboard):** like Overview/Home, the QA Dashboard
screen pulls from two different keys — `ora.quality` (the guard reports) and
`settings.switches` (the loop-beta kill switch sub-widget). Same AT3 filtering
implication as Overview.

## system.status / services / backups / resources / featuremap / codehealth / database / maintenance / howto
| Method | Path | File:Line | Old screen | New key | Level | Reason |
|---|---|---|---|---|---|---|
| GET | /admin/status/all | admin_health.py:135 | Cockpit (row 2) | system.status | V | — |
| POST | /admin/status/{id}/ack | admin_health.py:243 | Cockpit (row 2) | system.status | W | acking is reversible (re-fires) |
| GET | /admin/status/notifications, mark-read, mark-read/{id} | admin_health.py:274,301,315 | Notification Bell (row 1) | logs.notifications¹ | V (GET) / W (mark-read) | — |
| GET | /admin/deploy-readiness | admin_deploy_readiness.py:20 | Overview (row 6) | system.status | V | — |
| GET | /admin/integrations/health | admin_ops_config.py:366 | Integrations (row 27) | system.services | V | — |
| POST | /admin/integrations/refresh | admin_ops_config.py:407 | Integrations (row 27) | system.services | W | re-probes, no state change beyond a fresh read |
| POST | /admin/cache/purge | admin_ops_config.py:276 | Overview (row 6) | system.services | W | cache is rebuilt on next read — fully reversible |
| GET | /admin/cache/stats, /cache/analytics-stats | admin_ops_config.py:89,1168 | no screen | system.services | V | — |
| POST | /admin/cache/analytics-invalidate | admin_ops_config.py:1176 | no screen | system.services | W | same as cache purge |
| GET | /admin/observability/breakers, /{dep} | admin_observability.py:40,70 | no screen (#53) | system.services | V | duplicate of the QA guard17-breakers widget per PART-1's own flag — **recommend consolidating to one endpoint before AT1 tagging**, not required for Turn A |
| GET | /admin/guardrails | admin_ops_config.py:205 | no screen | system.services | V | — |
| POST | /admin/guardrails/{rule}/mode | admin_ops_config.py:229 | no screen | system.services | M | changes a platform guardrail's enforcement mode |
| POST | /admin/sentry/test | admin_ops_config.py:252 | no screen | system.services | W | fires a test event, no state change |
| GET | /admin/backups/status | backups_admin.py:41 | Overview (row 6) | system.backups | V | — |
| GET | /admin/system-stats?window_hours= | admin_analytics.py:222 | System Stats (row 45) | system.resources | V | — |
| GET | /feature-window/status | not traced — router file not opened this turn | System Map (row 44) | system.featuremap | V — **UNVERIFIED backend file**, same gap PART-1 flagged | — |
| GET | /admin/architecture, /code-surface, /eval-quality, /architecture-health, /duplication, /churn-risk | (see work.* section above) | Architecture (row 13) | system.codehealth | V | restated here only to confirm the key — already listed above, not double-counted |
| GET | /admin/health-score | admin_health_score.py:25 | Health Score Widget (row 4) | system.codehealth | V | — |
| POST | /admin/health-score/test-coverage/run | admin_health_score.py:31 | Health Score Widget (row 4) | system.codehealth | W | runs a check, no destructive effect |
| GET/POST | /admin/health-score/architecture-review | admin_health_score.py:83,94 | Health Score Widget (row 4) | system.codehealth | V (GET) / W (POST — adds a note) | — |
| GET | /admin/db-health | admin_ops_config.py:452 | Overview (row 6, "DB health") | system.database | V | resolves PART-1's earlier UNVERIFIED endpoint |
| GET | /admin/migrations/status | migrations_admin.py:87 | no screen (#63) | system.database | V | — |
| POST | /admin/migrations/apply (+2 more), /verify | migrations_admin.py:94,123,141,177 | no screen (#63) | system.database | **O** | explicit: "run a migration" |
| GET | /admin/maintenance | admin_maintenance.py:36 | Maintenance (row 31) | system.maintenance | V | — |
| POST | /admin/maintenance/settings | admin_maintenance.py:41 | Maintenance (row 31) | system.maintenance | **O** | explicit: "maintenance mode" |
| GET | /admin/maintenance/incidents | admin_maintenance.py:57 | Maintenance (row 31) | system.maintenance | V | — |
| — | Ops Recipes screen | OpsRecipes.jsx | Ops Recipes (row 43) | system.howto | V | no API calls at all — static content; PART-1's access-mismatch flag (non-admin topic gated behind AdminRoute) is worth revisiting once roles exist, since e.g. `support` role could reasonably get `howto` at View without needing any other system.* key |
| GET | /admin/system-health/* (11 calls incl. live-model-mode) | admin_analytics.py:429,901,2711 / admin_ops_config.py:859,907 / :944,:962 | System Health (row 30) | split — see below | — | — |

**System Health screen splits across 3 keys**, not one: council health →
`ora.council` (already listed above, not double-counted); drift-alerts,
webhook-fence, unlinked/reconcile/link installs → `users.github` (GitHub
install state is a users-domain concept, not system-domain, despite living on
a "System Health" page today); live-model-mode (GET only) → `settings.models`.
This is the third composite-page case (after Overview and QA Dashboard) — same
AT3 filtering implication.

¹ moved per SCREEN KEYS block: `logs: admin | turns | activity | events |
notifications` places "notifications" under `logs`, not `system`, even though
the bell renders on every admin screen — treating the key as governing the
underlying feed data, not the bell icon's visibility (the bell icon itself
should probably always render; what's INSIDE it is logs.notifications-gated;
flagging as an implementation note for AT2/AT3, not a Turn-A decision).

## settings.* (plans / paysetup / models / githubapp / video / hints / signin / team / switches / apikeys / usage)
| Method | Path | File:Line | Old screen | New key | Level | Reason |
|---|---|---|---|---|---|---|
| GET/POST | /admin/stripe-prices | admin_payments.py:381,461 | Settings (row 26) | settings.plans | V (GET) / M (POST) | price changes are platform-wide — ladder-gap note |
| GET/POST | /admin/stripe-config | admin_payments.py:211,315 | Settings (row 26) | settings.paysetup | **O** | explicit: "change payment keys" |
| GET/POST/PUT/DELETE | /admin/llm/configs* | admin_llm_config.py (all) | Settings (row 26) | settings.models | V (GET) / M (POST/PUT/DELETE — CRUD, platform-wide) | — |
| POST | /admin/llm/configs/{id}/set-active | admin_llm_config.py:112 | Settings (row 26) | settings.models | **O** | explicit: "activate a model" |
| POST | /admin/llm/configs/{id}/test | admin_llm_config.py:125 | Settings (row 26) | settings.models | W | a dry-run test call, no state change |
| GET/POST | /admin/github-app-config | admin_ops_config.py:507,602 | Settings (row 26) | settings.githubapp | V (GET) / **M, flag: holds a client secret — recommend owner-only** | treat like paysetup given secret-handling, final call is yours |
| GET | /admin/github-app-diagnostics | admin_ops_config.py:717 | no screen | settings.githubapp | V | — |
| GET/PUT/DELETE | /admin/video/secrets* | admin_video_pipeline.py:61,66,81 | Settings (row 26) | settings.video | V (GET) / **M, same secret-holding flag as githubapp above — recommend owner-only** | — |
| GET/PUT | /admin/llm/ora-chat-limits | admin_ora_chat_limits.py:35,41 | Settings (row 26) | settings.hints¹ | V (GET) / M (PUT) | tentative key — PART-1 didn't give a path for "thinking-hints config" specifically; this is the closest candidate found, **flag for Part 3 confirmation** |
| GET/POST | /ora-chat/pin/status, /pin/set | ora_chat.py | Settings (row 26) | — **N/A, see A2 below**: this is the founder's OWN personal ORA PIN, a sign-in mechanism, not a screen-gated admin feature; it should not carry a screen key at all — it's identity, not access | — | — |
| — | sign-in rules (rate-limit/lockout thresholds, password policy) | **no backend route found anywhere in the codebase** | n/a — new screen | settings.signin | — | this key has **nothing to tag today**; it's a future screen with no existing implementation. Default-closed per your own rule ("a new screen is Off for every role") handles this correctly with zero build needed in AT1 |
| — | team invite/manage | **does not exist yet — this is AT2's own deliverable** | n/a — new screen | settings.team | **O** (all of invite/change-role/suspend/remove, per your explicit list) | — |
| GET/POST | /admin/feature-flags, /{flag}/toggle | admin_ops_config.py:118,129,156 | Feature Flags (row 28) | settings.switches | V (GET) / M (POST/toggle — platform-wide) | — |
| GET/POST | /admin/gateway/keys | admin_gateway.py:53,38 | API Keys (row 29) | settings.apikeys | V (GET) / W (POST — creates a key, revocable) | — |
| POST | /admin/gateway/keys/{id}/revoke, /regenerate | admin_gateway.py:61,70 | API Keys (row 29) | settings.apikeys | W | reversible (regenerate issues a replacement) |
| PATCH | /admin/gateway/keys/{id}/limits | admin_gateway.py:89 | no screen (#55) | settings.apikeys | W | adjusts a rate limit, reversible |
| POST | /admin/gateway/test/mcp | admin_gateway.py:103 | API Keys (row 29) | settings.apikeys | W | test call only |
| GET/POST | /admin/gateway/oauth-clients, /{id}/revoke | admin_gateway.py:123,133 | API Keys (row 29) | settings.apikeys | V (GET) / M (revoke — kills one integration's access, closer to "affects one connection" than "one user" but nearest fit) | — |
| GET | /admin/mcp-usage | admin_analytics.py:2004 | MCP Usage (row 19) | settings.usage | V | — |

¹ flagged tentative mapping — not asserted with the same confidence as the
file+line-backed rows.

## logs.admin / turns / activity / events / notifications
| Method | Path | File:Line | Old screen | New key | Level | Reason |
|---|---|---|---|---|---|---|
| GET | /admin/audit/actions | admin_bin.py:798 | Audit (row 35) | logs.admin | **V, capped — your own rule: "logs are read-only for everyone (max level 1)"** | — |
| GET | /admin/audit | admin_analytics.py:73 | Audit (row 35) | logs.activity¹ | V (capped) | the "broader signals feed" toggle on the same screen reads from a different source than the admin-action log — split into its own key |
| GET | /admin/status/notifications (+mark-read) | admin_health.py:274,301,315 | Notification Bell (row 1) | logs.notifications | V (capped; mark-read is a V-level feed operation, not a Work-tier mutation, under "logs are read-only ... max level 1" — **flag**: literally marking read IS a write, so this is a small inconsistency between your "read-only" rule and the bell's existing mark-read button; recommend treating mark-read as allowed at View since it's the member's OWN inbox state, not data about others | — |
| — | `turns`, `events` keys | **no dedicated existing endpoint identified separate from what's counted above** | — | logs.turns, logs.events | — | likely map to loop-session event tails (already counted under work.tasks as /admin/loop-inspect) and/or ORA chat turn logs (ora.ask) — **flagging as not yet cleanly separated**, needs your input on whether these are meant to be distinct feeds from what exists, or aliases for existing data viewed through a "logs" lens |

¹ tentative — PART-1 named this "a broader signals feed" without further detail;
not opened this turn.

---
## Routes that fit NO key (confirmed, not candidates for any of the 56 keys)
1. **GET `/version`** (main.py, not `/admin`-prefixed) — has **no `require_admin` gate at all**, per PART-1 row 2's own note. This is a pre-existing, unrelated-to-this-project finding: it is public today. Not in scope to fix under this plan, flagging because A1 asked for routes that fit no key, and "no key because not actually an admin route" is a distinct reason from the others below.
2. **POST `/admin/context-bridge`** (admin_context_bridge.py:112) — written to by a Chrome extension, never read by any screen; correctly has no key, by design (PART-1 #52).
3. **POST `/admin/dev/kill-supervised-task/{name}`, `/admin/dev/clear-supervised-postmortem/{name}`** (admin_ops_config.py:1188,1258) — 404 unless `AUREM_TEST_MODE=1`; deliberately hidden dev-only tooling (PART-1 #57), out of scope for any role.
4. **GET `/admin/connect-diagnostic/{user_id}`, `/admin/connect-diagnostic/audit/stale-github-urls`** (admin_connect_diagnostic.py:69,226) — a direct-URL support/debug tool with no nav entry (PART-1 #51). Closest real fit would be `users.all` or `support.tickets`, but since no screen renders it, under "closed by default: an admin route with no screen key is owner-only" it stays owner-only until/unless you wire it to a screen.
5. **`/ora-chat/pin/status`, `/pin/set`** — the founder's personal sign-in PIN, not a gated admin feature (see settings.* table note above and A2 below).

---
## Summary counts
- Screens/endpoint-groups inventoried: 47 (Part 1a) + 16 (Part 1b) = 63, unchanged from PART-1.
- Individual routes tagged with a key + level above: **~140** (exact count depends on how you count the `admin_qa.py` "~18 more guardN-*" reads, which PART-1 itself didn't enumerate one-by-one).
- Confirmed **owner-only** routes: 11 (delete user, bulk-revoke, 2× erase-ORA-memory, run-migration ×3, maintenance-mode-settings, activate-model, kill-switch, change-payment-keys) + all of settings.team (future, 4 actions) = **15 once AT2 exists**.
- Keys with **zero existing backend route** (new screens, default-closed, nothing to migrate): `settings.signin`, `settings.team`.
- Open questions needing your call before AT1 locks the level table (see flagged rows above): the "bulk but non-destructive" ladder gap (6 routes), the two secret-holding settings screens I'd recommend bumping to owner-only, and the tentative `settings.hints` / `logs.turns` / `logs.events` mappings.
