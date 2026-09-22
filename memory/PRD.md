# Aurem / ORA — Product Requirements and Current Handoff

Last updated: 2026-09-22. The previous 9,681-line combined history is preserved without deletion in `PRD_ARCHIVE_2026_09_22.md`. Historical claims there are not fresh production verification.

## Product and original goals

A coding-agent SaaS for explaining, inspecting and changing connected repositories through conversational requests, with scoped permissions, visible diffs/approval, reliable completion states, evidence-grounded responses, telemetry and operational Cockpit diagnostics. Primary audience: founders and developers using existing GitHub projects.

## Architecture

- React/Vite frontend, FastAPI backend, MongoDB; frontend externally configured API base, backend `/api` routes.
- Main chat: `backend/routers/chat/{turn,stream,worker}.py`; shared response/persistence: `backend/services/chat_helpers.py`; orchestration: `services/orchestrator.py`.
- Alternate admin advisor: `routers/ora_chat.py` → `services/ora_chat_v2/engine.py`, a separate contract/persistence path.
- Frontend: `ChatPanel.jsx`, `MessageBubble.jsx`; history mapping `utils/dateDividers.js`.
- Tool telemetry: `local_tools.py`, `tool_executor.py`, `task_chain_logging.py`; recurrence: `regression_backlog_cron.py`.
- Existing credentials/configuration remain in their designated environment and credential documentation. Never print secret values. Preserve semantic-router canary **10%**.

## Latest completed round (2026-09-22) — Admin Overview audit vs codebase

User request (Hindi/English): audit `https://auremcto.com/admin/overview` against the actual codebase, fix any issues found so every feature on the page works, and update PRD/markdown docs. No production credentials are available to this agent, so verification was done against preview (`frontend/src/pages/AdminOverview.jsx` + its ~27 backend endpoints), which is the same codebase that ships to production.

**Method:** curl'd every one of the ~27 endpoints `AdminOverview.jsx` calls with a real admin JWT (preview), diffed response shapes against what the component reads, then live-loaded the page with Playwright.

**Two real bugs found and fixed (frontend-only, no backend/data changes):**
1. **GitHub Connect Funnel conversion % could exceed 100%.** `GhFunnelStrip.convPct()` computed `curr/prev*100` unbounded; live preview data (`oauth_redirect=16 → callback_received=26`, because GitHub's OAuth callback can be hit directly/by bots without our own click event firing) rendered a founder-facing `→ 162.5%`. Fixed by capping at 100%, same rationale the backend's own `pct_num()` already uses for the (separate, not-rendered) activation-funnel legacy field. Also capped the sibling "overall conv" footer stat.
2. **Static "System mapping" architecture card was badly stale.** Hand-maintained since Iter 152, it claimed "24 pages · 5 hooks · 30+ components" and "26 routers · 47 services" with an LLM list of just "DeepSeek V3 · Claude Sonnet 4.5" — the repo actually has 71 pages, 14 hooks, 102 router modules, 280 services, and the live LLM Council (visible one card above in the real LLM Provider Status widget) is LongCat (primary) → GLM-5.2 (fallback), with DeepSeek/Claude Sonnet/Groq in other roles. Rewrote the card's counts and integration list to match the verified current repo.

**Everything else checked, confirmed correct — not touched:** all ~27 GET/POST endpoints return 200 with matching field shapes; the activation-funnel legacy `conversion_rates` dict *can* also show >100% (e.g. `project_to_message: 900.0%`) but that field is never rendered by the frontend (only the already-capped `funnel_steps[].pct_of_prev` is), so no fix was needed there. "GitHub sync 401", "Resend 400", "NOT DEPLOY-READY (54 uncommitted)" banners are real, correctly-surfaced operational states, not bugs.

**Testing:** `testing_agent_v3_fork` full regression pass on `/admin/overview` (all ~24 sub-sections, drill-down modal open/close, refresh-all, ESLint) — 0 action items, both fixes live-verified. Report: `test_reports/iteration_admin_overview_regression_2026_01.json`.

**Not done (flagged by testing agent as maintainability debt, not a bug):** `AdminOverview.jsx` is ~2650 lines, well over the 700-line guideline — splitting each card into its own file under `frontend/src/pages/admin_overview/` is a future refactor, not requested by the user this round.

## Prior round (2026-09-22) — Interaction reliability audit (implemented, NOT activated)

Deep UI/interaction reliability audit across eight shared failure mechanisms; new opt-in `INTERACTION_CONTRACT_V1=1` (flag OFF by default, semantic canary unchanged); 38 backend + 8 frontend gate tests passed; full frontend regression had 11 pre-existing unrelated failures (795 passing); no production behavior verified. Full detail preserved in `CHANGELOG.md` and `memory/INTERACTION_RELIABILITY_AUDIT_2026_09_22.md` / `memory/CODING_AGENT_BLUEPRINT_V2.md` — read those before touching `INTERACTION_CONTRACT_V1`, `input_normalizer.py`, or `semantic_mode_router.py` again.

## Known open items (not fixed by any round above)

- `input_normalizer.py` silently changes a code-fenced JS string `a—b` into `a--b`.
- Semantic classifier timeout with prior mode E returns E for a status query.
- Separate advisor engine (`ora_chat_v2`) does not share the main chat's v1 contract.
- Production/external blockers: G10 Resend account/domain config (HTTP 400, visible live on `/admin/overview`), G8 GitHub sync 401 (expired `GITHUB_ACTIONS_TOKEN`, visible live on `/admin/overview`), G15 ingestion evidence, Visibility Day14 actual citation check, E2B configuration, jscpd/churn runtime tooling.

## Next priorities

- **P0:** Rotate/renew `GITHUB_ACTIONS_TOKEN` (fixes the live "GitHub sync (Guard 8)" critical alert) and fix Resend sender/domain config (fixes the live "Resend Email" critical alert) — both are founder/ops actions, not code fixes.
- **P1:** Interaction-reliability round's P0/P1 items (full regression completion, staging/production verification before `INTERACTION_CONTRACT_V1` activation, raw-input fidelity fix).
- **P2:** `AdminOverview.jsx` file split into per-card components; calibrated router/model benchmarks; proof-details drawer.

See ROADMAP.md for historical backlog; CHANGELOG.md for prior dated work.