# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` is the canonical rule set (stack, conventions, testing requirements, security, completion-report format). Read it first; this file only adds commands and the cross-file architecture. `AGENTS.md` asks that tool-specific files reference it rather than restate it. Its "current honest state" section (known failing frontend tests, Auto-QA never run) goes stale quickly, so re-verify before quoting it.

## Commands

Backend (`backend/`, FastAPI on 8001, Mongo via Motor):
```bash
cd backend
python3 -m pytest tests/                                   # default run
python3 -m pytest tests/test_iter116_init_collections.py   # one file
python3 -m pytest tests/<file>.py::<test_name>             # one test
python3 -m pytest -m legacy                                # deferred pre-iter267 failures (own lane)
ruff check .                                               # from backend/ (AGENTS.md runs `ruff check backend/` from the repo root)
```
`pytest.ini` default `addopts` excludes `flaky`, `llm_judge`, `legacy` and `known_fail_audit_2026_08`; `source_of_truth` tests run normally and CI re-runs them in isolation. `live_env` tests auto-skip when `{BASE}/api/health` is unreachable. `asyncio_mode = auto`, so async tests need no decorator. Tests import with `backend/` as the root and some legacy tests hardcode `/app` paths (CI symlinks the workspace to `/app`).

Frontend (`frontend/`, Vite + Vitest, jsdom):
```bash
yarn dev            # port 3000
yarn test           # vitest run (all)
npx vitest run src/components/__tests__/<File>.test.jsx   # one file
yarn lint           # eslint src
yarn build          # vite build && seo-prerender.mjs (CI also greps the log for SyntaxError / Module not found)
yarn test:visual    # Playwright visual regression
```
Vitest has no React plugin, so tests use classic JSX. A test that mocks `../../lib/api` must export every function the component imports (e.g. `fetchMyAdminAccess`), or it throws at render.

In the hosted preview, supervisor owns both processes (`sudo supervisorctl restart backend`); don't launch `uvicorn`/`vite` directly there.

## Architecture (what takes several files to see)

- **Entry point.** `backend/server.py` only re-exports `app` from `backend/main.py`. `main.py` is huge: lifespan startup, ~100 `include_router` calls, and many ad-hoc background tasks. Routers are mounted under both `/api` and `/api/aurem-dev` depending on the router, so check the `include_router(..., prefix=...)` line before assuming a path.
- **Request flow.** `routers/` → `core/intent_gateway.py` classifies → `core/parliament/` picks the agent → `services/` does the work (README: "no endpoint may call an agent directly"). `services/local_tools.py` holds ORA's read/write/search-repo tools; `services/cto_projects_helpers.py` and `services/cto_pipeline_steps.py` hold the edit pipeline and its scope-creep (import-preservation) gate. Vanguard (`services/vanguard_scanner.py`) is a separate, regex-based security scan.
- **Ship flow.** Fixes go to the user's repo as a PR, never a direct push. The fixed-findings ledger (`cto_fixed_findings`) hides an applied fix from rescans until its PR merges. A task is deducted only on success (`services/scan_fix_quota.py::record_scan_fixes`).
- **Confirmations.** A "yes" resolving a pending action is a deterministic state transition in `services/actions/pending_action.py`, never an LLM turn.
- **Mongo index bootstrap is layered.** `scripts/init_prod_collections.py` (`_BOOTSTRAP_SPEC`) runs inline in lifespan; `services/db_indexes.py` (unique/dedup indexes) and `services/query_indexes.py` (read-path, non-unique) run in a background boot-maintenance task so a slow index build can't delay startup. `GET /admin/db-health` reports bootstrap and query-index status. A compound index only serves queries that use its leading field, so a new query shape on another field needs its own entry.
- **Caching** is mostly in-process; Redis is optional (`REDIS_URL`) and used by `services/admin_analytics_cache.py` and the rate limiter, which fall back to memory.
- **Frontend.** React SPA; routes are all declared in `frontend/src/App.jsx`. API access goes through `src/lib/api.js` (auth token in `localStorage` key `aurem_token`, admin access via `fetchMyAdminAccess`). `src/pages/Admin.jsx` plus `adminNav.js` drive the admin console, and `components/nav/RailShell.jsx` is the logged-in shell. Pages are code-split, so a page crash shows the route error boundary ("Something went wrong loading this page") rather than a blank app.

## CI gates that bite

`.github/workflows/ci.yml` and `quality-gate.yml` fail a PR for more than test failures. These are the ones that surprise people:
- Touching `backend/routers/`, `backend/services/` or `frontend/src/components/` requires also changing a test file.
- Coverage ratchet + touched-file floor (`scripts/ci_check_coverage_ratchet.py`), regression-baseline lock, new-file bloat guard (`scripts/ci_check_new_bloat.py`), test-style check (`scripts/ci_check_test_style.py`, rejects static-grep-only tests), raw-exception-leak and machinery-leak copy checks, pricing-copy lint, and secret scan of the rendered build.
- Override markers (`[bloat-approved]`, `[coverage-approved]`, `# static-grep-ok`, `[docs-only]`, `[no-test-needed]`) need explicit reviewer sign-off. Never add one yourself.
- Bug fixes need a `test_regression_iter<N>_<description>` test (see `AGENTS.md`).

## Working agreements for this repo

Owner is the only one who merges, deploys, changes repo settings/secrets, or turns on a production flag. Keep one PR to one change, and report what was verified versus not. Production (`auremcto.com`) only changes on a manual redeploy, so a merged fix is not live until then. Don't push to Nikhat's or Emergent's branches. Emergent keeps its own checkout and can diverge from GitHub, so fetch before assuming `main` is current.
