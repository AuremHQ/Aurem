# 01 — SYSTEM OVERVIEW & CORE DESIGN PATTERNS
(Load this FIRST in every session. High-level map — read before touching any layer. Files 02–07 cover each layer in depth.)

## HIGH-LEVEL ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────┐
│                      USER (Browser / PWA)                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS
┌──────────────────────────▼──────────────────────────────────┐
│              FRONTEND — React SPA (Port 3000)                │
│   Tailwind + Shadcn UI + Context API + SSE Streaming         │
│   88 pages, 279 components                                   │
└──────────────────────────┬──────────────────────────────────┘
                           │ /api/* (Kubernetes Ingress)
┌──────────────────────────▼──────────────────────────────────┐
│              BACKEND — FastAPI (Port 8001)                   │
│   102 Routers → Core (Parliament) → 345 Services → External  │
│   Entry: backend/main.py (all include_router calls live here)│
└──────────┬───────────────┬───────────────┬──────────────────┘
           │               │               │
     ┌─────▼─────┐   ┌─────▼──────┐  ┌─────▼──────────────┐
     │  MongoDB  │   │ GitHub App │  │ LLM Providers       │
     │ (Motor)   │   │ (install-  │  │ OpenRouter/Groq/    │
     │           │   │  based)    │  │ Anthropic (Emergent)│
     └───────────┘   └────────────┘  │ Council A primary:  │
                                     │ claude-sonnet-4.5   │
                                     └─────────────────────┘
```

## LAYER RESPONSIBILITIES (one line each)
- **Frontend**: React SPA — chat UI, dashboards, wizards, admin suite, Personal Track (T0-T4), ORA Chat (`/ora`). Talks to backend only via `/api/*` using `REACT_APP_BACKEND_URL`. → file 02
- **Backend Core ("Parliament")**: intent classification + multi-agent routing (`backend/core/`). → file 03
- **Backend Routers**: 102 `include_router` registrations (~120 router files, `backend/routers/`) — auth, scanning, fixing, repo, business, deploy, admin, Personal Track, ORA Chat, Supabase, managed DB, trust & verification. → file 03
- **Backend Services**: 345 modules (`backend/services/`, incl. `ora_chat/`, `ora_chat_v2/`, `seo/`, `actions/`, `http/`, `llm/`, `video_pipeline/`, `visibility/` subpackages) — AI orchestration, scanners, fix engine, repo intelligence, safety guards, learning, billing, Personal Track scaffolding, Supabase provisioning, trust & verification. → file 04
- **Data Layer**: MongoDB via Motor — users, projects, tasks, fixed-findings ledger, ORA sessions, house rules, audit logs. → file 05
- **External Integrations**: GitHub App (installation-based; PAT support removed), LLM providers (OpenRouter/Anthropic/DeepSeek/Sonar), Google Auth, Stripe, Vercel, Supabase, Meta Pixel, SSE. → file 05
- **Business Logic**: tier/quota system (`subscription_tiers.py` + `scan_fix_quota.py`), Personal Track quotas (`personal_track_quotas.py`). → file 06
- **See also**: `/app/memory/CODEBASE_MAP.md` for a single-file, always-fresh structural snapshot with subsystem-level detail. `07_demo_drift.md` for the marketing-demo reality contract.

## THE 5 CORE DESIGN PATTERNS
(Check which pattern your change touches BEFORE writing code. Follow the existing convention — never invent a parallel one.)

1. **Parliament Pattern** — every AI request is classified first by `core/intent_gateway.py`, then routed by `core/parliament.py` to a specialized agent (Council / Debugger / Auditor / Engage). No endpoint may call an agent directly, bypassing intent classification.

2. **Fixed-Findings Ledger** — an applied fix is recorded in `cto_fixed_findings` (via `services/fixed_findings.py`) and stays hidden from rescans until its PR actually merges. This prevents the health score from falsely regressing. Never mark a finding permanently resolved before merge confirmation of its `commit_sha`.

3. **Quota-as-Tasks** — 1 fix = 1 task, always. No severity-based pricing. Gating is by TOOL and FEATURE (bulk), enforced in `services/scan_fix_quota.py`. Deduction happens ONLY on successful fixes via `record_scan_fixes()` — a failed fix never burns a task. Scan-fix usage rolls into the same monthly task meter as chat tasks (`services/usage.py`).

4. **Local Snapshot Cache** — `.aurem_cache` holds a local git-tree snapshot so scans are fast and GitHub secondary rate limits aren't hit. Any new repo-reading feature reads from this cache first, never GitHub directly.

5. **Guard Layers** — `services/hallucination_guard.py` and `services/citation_guard.py` verify LLM output before any fix is applied. An LLM-generated fix that hasn't passed BOTH guards must never touch a user's repo.

## RULES FOR THE AI DEVELOPER (hard constraints)
1. Identify which of the 5 patterns your task touches and follow its convention. If a change would break a pattern, STOP and flag it explicitly before implementing.
2. All backend routes MUST be prefixed with `/api`. Frontend must use `process.env.REACT_APP_BACKEND_URL`; backend must use `MONGO_URL`/`DB_NAME` from `backend/.env`. No hardcoded URLs, ports, or credentials anywhere.
3. Never expose `github_token` (from `cto_projects`) in logs, API responses, or the frontend.
4. Never bypass `intent_gateway.py` → `parliament.py` for AI request routing.
5. Never apply an LLM fix without both `hallucination_guard` and `citation_guard` passing.
6. Two environments exist: PREVIEW (dev, editable) and PRODUCTION (https://auremcto.com, requires user redeploy). Code changes only affect preview until redeployed.
