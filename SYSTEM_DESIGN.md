# AUREM — System Design (living blueprint)

> Status legend: ✅ exists (evidence in repo) · ⚠️ partial / undocumented · ❌ missing.
> Facts below were measured on 2026-09-07 (`backend/` at `/app`). Numbers are `grep`/`ls`
> counts, not estimates. Update this file when the architecture changes — the
> automated guard is `backend/scripts/architecture_health.py --fail-on-new`.

---

## 0. One-screen picture

```
USER BROWSER (React 18 + Vite, port 3000)
   │  HTTPS · Bearer JWT (cto_services/auth.py) · SSE for chat/loop streams
   ▼
FASTAPI (main.py, 94 include_router, prefix /api/aurem-dev)   ← preview: Emergent pod
   │                                                            ← prod: auremcto.com (founder: Render)
   ├─ routers/ (97 files, 589 routes)
   │    auth · github_oauth/google_oauth · cto_projects/ (pkg) · chat/ (pkg) · loop
   │    visibility · payments · admin* (≈40 admin routers) · deploy · security_scan …
   ├─ services/ (259 files) — business logic
   │    loop_engine (plan→generate→verify→ship) · orchestrator · llm/ (OpenRouter→Groq fallback)
   │    pat_vault (GitHub App tokens) · visibility/ · vanguard · health_checks (25 gates)
   │    retry_guard (circuit breakers) · http/client.ext_client (all outbound HTTP)
   ├─ core/ — boundaries, errors, observability, intent_gateway, tool_router, parliament
   ├─ MongoDB (MONGO_URL/DB_NAME) — ~200 collections touched from code
   └─ EXTERNAL: GitHub App API · OpenRouter/Groq/Emergent LLM · Stripe · Resend
                Firecrawl · E2B sandbox · Sentry · Redis (rate limiter, optional)
```

---

## 1. Architecture

| Item | Status | Where / evidence |
|---|---|---|
| Layering rule (routers → services → cto_services/core; services never import routers) | ✅ enforced | `services/architecture_health.py` boundary rules; **0 violations, 0 circular imports** (2026-09-07) |
| Router inventory | ✅ | 97 router files, 589 `@router` routes; `main.py` registers 94 |
| Service inventory | ✅ | 259 service modules; `memory/SYSTEM_INVENTORY.md` (seeded 2026-07-27, partially stale) |
| God-file list + refactor plan | ⚠️ known, not done | `ChatPanel.jsx` 6.1k, `loop_engine.py` 4.9k, `main.py` 3.5k, `orchestrator.py` 2.9k, `local_tools.py` 2.7k · plan in CHANGELOG 2026-09-07 (app factory, loop_engine by phase, ChatPanel split) |
| Hub modules (change = whole-app change) | ✅ measured | `cto_services/db.py` imported by 146, `cto_services/auth.py` by 94 |
| Architecture diagram (this doc) | ✅ new | §0 |
| C4 / sequence diagrams for the 3 core flows | ❌ | see §2 text versions; no rendered diagrams |

## 2. Data flow (text sequence diagrams)

**Chat turn** — `POST /chat/stream` → `routers/chat/stream.py` → intent gateway (`core/intent_gateway`) → `services/orchestrator.chat_with_tools` → `services/llm/_meta.call_llm_with_meta` (OpenRouter, Groq fallback, cost cap `llm_usd_cap`) → tool calls (`local_tools`, `browser_tools`, GitHub read via `pat_vault` token) → SSE events → `chat_sessions.turns` persisted (`services/chat_helpers._persist_turn`) → `ora_chat_usage` metered.

**Loop ship** — `POST /loop/start` → ambiguity gate → `LoopEngine` (plan → awaiting confirmation → generate → verify (`loop_independent_verifier`) → security scan → ship) → `github_api_writer.commit_files` on a branch / draft PR (Visibility Kit path) or direct commit (legacy CTO path ⚠️ still commits to `main` while labelled "ship-via-PR") → `_emit` SSE → `loop_sessions`, `loop_run_log`, `loop_audit_log`.

**GitHub App install/webhook** — GitHub → `POST /github/app/webhook` (HMAC verified, `routers/github_app.py:534`) → `github_installations` → `pat_vault.get_repo_token` mints installation tokens (no PAT/OAuth fallback since 2026-06).

**Payments** — Stripe Checkout (`routers/payments.create_checkout_session`, registered into `services/checkout_gateway`) → webhook (`stripe.Webhook.construct_event`) → `cto_payments`, tier on `dev_users`.

| Item | Status |
|---|---|
| Text flows for chat / loop / GitHub / payments | ✅ above |
| Flows for Visibility Kit apply, Vanguard scan, onboarding scan, deploy verify | ❌ not written |

## 3. Data model / DB design

| Item | Status | Evidence |
|---|---|---|
| Collection catalogue with fields | ⚠️ | ~200 collections referenced; top: `dev_users`(196 refs) `cto_projects`(131) `cto_tasks`(72) `loop_sessions`(57) `chat_sessions`(41) `github_installations`(32) `aurem_cto_deploy_runs`(31) … No single schema doc; Pydantic models scattered per router |
| Indexes | ⚠️ | 85 `create_index` call sites, but only 3 modules expose `ensure_indexes(db)`; no central index registry, no startup index audit |
| Migrations | ⚠️ | `backend/migrations/` has **3** files (`003_visibility_kit` etc.); no runner that records applied migrations; prod migration 003 was run manually |
| Relations / ownership (`user_id`, `project_id` keys) | ✅ convention | every tenant collection keyed by `user_id`; `require_project_owner` style checks in routers |
| ERD | ❌ | none |
| Data retention / TTL policy | ⚠️ | `loop_plans` 24 h TTL (`PLAN_TTL_S`), `oauth_states` TTL; no policy for chat history, logs, audit |

## 4. API design

| Item | Status | Evidence |
|---|---|---|
| Prefix & versioning | ⚠️ | everything under `/api/aurem-dev/…`; only `/api/v1/health` is versioned |
| Auth on endpoints | ✅ | `current_dev` (JWT) / `require_admin` + router-level `require_admin_dep` on all admin routers (G21 gate green) |
| Standard error envelope | ⚠️ | `core/errors.py` exists; many routers still return ad-hoc `{"ok": false, "error": …}` with HTTP 200 (Visibility Kit apply is one) |
| Rate limiting | ✅ | `services/rate_limiter.py` — Redis sliding window (Lua) with in-memory fallback; login 10/min/IP (`LOGIN_RATE_PER_MIN`) |
| OpenAPI | ✅ auto | FastAPI `/docs`; 589 routes, tags present on most routers |
| Pagination | ⚠️ | 34 routers use `.limit()`; no shared cursor/page contract |
| Idempotency keys for writes (ship, apply, checkout) | ⚠️ | loop uses `loop_locks`; apply/checkout rely on GitHub/Stripe idempotency, not documented |

## 5. Security design

| Item | Status | Evidence |
|---|---|---|
| AuthN | ✅ | JWT (HS, `JWT_SECRET`), GitHub OAuth (login only), Google OAuth; sessions revocable (`revoke-all-sessions`, `jwt_revocation` tests) |
| AuthZ | ✅ | founder/admin via `FOUNDER_EMAILS` + `is_admin`; tier gates (`subscription_tiers`); shared predicate `cto_services.auth.is_admin_or_founder` |
| Secrets at rest | ✅ | `services/vault.py` — HKDF per-customer Fernet key wrapped by `AUREM_MASTER_KEY`; GitHub tokens never stored (minted per call) |
| In-transit | ✅ | HTTPS at edge; outbound via `ext_client` (TLS, timeouts) |
| Webhook signatures | ✅ | GitHub HMAC, Stripe `construct_event` |
| Tenant isolation | ✅ tested | `test_iter212m169_bin_context_isolation`, `…170_ora_context_isolation` (cross-user poison tokens rejected) |
| Secret scanning of customer repos | ✅ | `services/security_text_scanner`, Vanguard |
| Dependency CVE gate | ✅ | G15 (`pip-audit` + `yarn audit`, allowlist with expiry) |
| Security headers / CSP | ⚠️ | CORS configured in `main.py`; CSP/HSTS not verified in repo |
| Threat model document | ❌ | none |
| Secret rotation runbook | ❌ | Resend key currently invalid in prod — no rotation procedure written |

## 6. Scalability

| Item | Status | Evidence |
|---|---|---|
| Process model | ⚠️ | single Uvicorn process per instance (supervisor in preview); horizontal scaling assumed from host, not designed (in-memory caches/locks are per-process) |
| Caching | ⚠️ | `mongo_swr_cache`, `repo_status_cache`, `scan_cache` (tree-SHA keyed text cache) — all **in-process**; Redis used only by rate limiter |
| DB scaling | ❌ | single Mongo; no read-replica/sharding plan; hot collections unindexed audit missing |
| Background work | ⚠️ | asyncio tasks in-process (`warm_start_jobs`, crons in `services/*_cron.py`); no queue/worker separation → a restart kills in-flight loops (loop resume exists) |
| Load test numbers | ❌ | none recorded |

## 7. Reliability / fault tolerance

| Item | Status | Evidence |
|---|---|---|
| Circuit breakers + retry | ✅ | `services/retry_guard.CircuitBreaker`, `call_with_retry`; `llm_circuit_breaker`, `llm_cost_breaker` |
| LLM fallback | ✅ | OpenRouter → Groq; Emergent key path; USD cap |
| Health checks | ✅ | `/api/health`, `/api/v1/health`, 25 cockpit gates (`services/health_checks.py`), G17 breakers, G19 restart-loop, G20 incidents |
| Auto-heal | ✅ | `services/repo_heal.py` (network retry, fresh App token, rename detection) |
| Backups / drills | ✅ | weekly restore drill + rollback drill, v2 ledger (Data Handling gate) |
| Graceful degradation matrix (Mongo down / GitHub down / LLM down → user sees what) | ❌ | behaviour exists in code paths but no written matrix |
| SLOs / error budget | ❌ | none defined |
| Second alert channel (Resend only) | ❌ | founder alerts fail when Resend key invalid (current state) |

## 8. Performance

| Item | Status | Evidence |
|---|---|---|
| Async I/O everywhere | ✅ | FastAPI + motor + httpx |
| Latency tracking | ✅ | p95 in Health Score (`perf_samples`), R10_TIMING logs in chat stream |
| Repo text cache keyed by tree SHA | ✅ | `services/repo_text_cache` + `scan_cache` |
| N+1 fixes | ✅ (several) | e.g. automations webhook (Iter 212m-70) |
| Index coverage review of hot queries | ❌ | no `explain()` audit |
| Frontend bundle budget | ⚠️ | Vite; `ChatPanel.jsx` 6.1k lines, no size budget in CI |

## 9. Monitoring / observability

| Item | Status | Evidence |
|---|---|---|
| Error tracking | ✅ | Sentry (`SENTRY_DSN`, `main.py:206`) |
| Structured logging + request id | ✅ | `core/observability.py`, `ext_client` request-id/UA |
| Metrics | ⚠️ | admin analytics + health score + gates (Mongo-backed); no Prometheus/OTel export |
| Tracing | ❌ | no distributed tracing |
| Alerts | ⚠️ | founder alerts via Resend (G10) — single channel, currently failing |
| Cockpit | ✅ | `/admin` System Overview: 25 gates, Health Score (9 dimensions) |

## 10. Deployment / DevOps

| Item | Status | Evidence |
|---|---|---|
| Environments | ✅ | preview (Emergent pod, `REACT_APP_BACKEND_URL`), prod `auremcto.com`; `.env` per env |
| CI | ⚠️ red | `.github/workflows/`: `ci.yml` (0/183 green pushes in 30 d), `quality-gate.yml` (invariants lane fixed 2026-09-06, 704/704 locally), visual regression, G15/G21 jobs, weekly QA, rebaseline |
| CD | ⚠️ | prod deploy is manual via Emergent toolbar (founder); `deploy.yml`/`auto_deploy.yml` exist but not the live path; founder states Render + Mongo Atlas for hosting — **no `render.yaml` in repo** |
| Migrations in deploy | ❌ | not part of deploy; run by hand |
| Rollback | ✅ | rollback drill gate G12 + `user_rollback` router |
| Infra as code | ❌ | none |
| Runbooks (deploy, rotate key, restore) | ⚠️ | restore/rollback drills automated; no human runbook file |

## 11. Data integrity / consistency

| Item | Status | Evidence |
|---|---|---|
| Tenant isolation | ✅ | see §5 |
| Concurrency on loops | ✅ | `loop_locks`, atomic `find_one_and_update` upserts (`incident_log`, `process_recovery`) |
| Multi-document transactions | ❌ | `start_session/with_transaction` used **0** times — payments+tier updates are two writes without a transaction |
| Idempotent webhooks | ⚠️ | Stripe event ids not de-duplicated in code paths reviewed |
| Schema validation at write | ⚠️ | Pydantic on request bodies; no Mongo JSON-schema validators |

## 12. API contracts (frontend ↔ backend)

| Item | Status | Evidence |
|---|---|---|
| Contract tests | ✅ (partial) | golden stream regression (`tests/_golden/stream_refactor_2026_09_08.json`), `test_iter312` start_loop shape, frontend_sync tests |
| Shared types / OpenAPI-generated client | ❌ | frontend calls `api.get/post` by string path; no generated types |
| Versioned event schema for SSE | ⚠️ | event names stable by test, no schema file |
| `ok:false`-with-200 convention documented | ❌ | callers must check `data.ok` (VisibilityKitPanel bug of 2026-09-06 came from this) |

---

## Gap list (what to build, in priority order)

1. **Error envelope + `ok:false` audit** (§4/§12) — one shape, HTTP status matches outcome.
2. **Index registry + startup audit** (§3) — one `ensure_indexes()` per collection, `explain()` on the 10 hottest queries.
3. **Migration runner** that records applied versions and runs on deploy (§3/§10).
4. **Degradation matrix + SLOs** (§7) — Mongo/GitHub/LLM/Stripe down → defined user-visible behaviour.
5. **Second alert channel** (Slack/Telegram) + secret-rotation runbook (§5/§9).
6. **Transactions or compensations** for payment → tier writes; Stripe event de-dup (§11).
7. **Move in-process caches/locks to Redis** before horizontal scaling (§6).
8. **God-file refactor** per CHANGELOG plan; keep `architecture_health --fail-on-new` green (§1).
9. **CI green lane**: `ci.yml` boots its own backend, live-URL tests in a separate lane (§10).
10. **Generated API types** for the frontend + SSE event schema file (§12).
11. **Threat model + ERD + rendered C4 diagrams** (§1/§3/§5).
