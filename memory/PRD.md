# Aurem / ORA — Product Requirements and Current Handoff

Last updated: 2026-09-23. The previous 9,681-line combined history is preserved without deletion in `PRD_ARCHIVE_2026_09_22.md`. Historical claims there are not fresh production verification.

## Product and original goals

A coding-agent SaaS for explaining, inspecting and changing connected repositories through conversational requests, with scoped permissions, visible diffs/approval, reliable completion states, evidence-grounded responses, telemetry and operational Cockpit diagnostics. Primary audience: founders and developers using existing GitHub projects.

## Architecture

- React/Vite frontend, FastAPI backend, MongoDB; frontend externally configured API base, backend `/api` routes.
- Main chat: `backend/routers/chat/{turn,stream,worker,watchdog,stream_state}.py`; shared response/persistence: `backend/services/chat_helpers.py`; orchestration: `services/orchestrator.py`.
- Reliability layer (NEW 2026-09-23): `backend/services/timeout_clocks.py` (4-clock timeout tracker), `backend/services/chat_reliability.py` (durable `chat_request_ledger` + idempotent-retry replay + stale-row reconciler cron).
- Alternate admin advisor: `routers/ora_chat.py` → `services/ora_chat_v2/engine.py`, a separate contract/persistence path (NOT covered by INTERACTION_CONTRACT_V1 or the reliability layer above).
- Frontend: `ChatPanel.jsx`, `MessageBubble.jsx`; history mapping `utils/dateDividers.js`.
- Tool telemetry: `local_tools.py`, `tool_executor.py`, `task_chain_logging.py`; recurrence: `regression_backlog_cron.py`.
- Existing credentials/configuration remain in their designated environment and credential documentation. Never print secret values. Preserve semantic-router canary **10%**.

## Latest completed round (2026-09-23) — Reliability blueprint: INTERACTION_CONTRACT_V1 activation + P1 gap closure

User provided a detailed "Bounded, Honestly-Labeled Risk Engineering" reliability blueprint (project isolation, typed contracts, durable execution/retries, adversarial testing, staged verification, observability, recurrence prevention, user-facing reliability) and asked: keep what already exists, build what's missing, make it fully E2E working, and provide proof.

**Audit finding:** ~70% of the blueprint was already implemented (project isolation exploit tests, rollback manager, task state machine, SSE replay buffer, evidence pointer, failure taxonomy, retry guard, incident log — all pre-existing, re-verified passing). The single biggest gap: `INTERACTION_CONTRACT_V1` (typed response envelope, evidence-bound completion) was fully built but **disabled** and never E2E-verified, with 3 named P1 sub-gaps.

**Approved scope this round, all completed:**
1. **Activated `INTERACTION_CONTRACT_V1=1`** in `backend/.env` (was absent/OFF). Re-captured `tests/_golden/stream_refactor_2026_09_08.json` (verified the ONLY diff was the `interaction` envelope going from `None`→populated, as expected).
2. **4 distinct timeout clocks** (`services/timeout_clocks.py`): `connect` / `first_token` / `stream_idle` / `hard_deadline`, replacing the old single elapsed-since-start soft/hard pair's conflated signal. `stream_idle` is a genuinely new capability (resets on real progress, catches "1 tool call then silence" which the old mechanism never caught). `hard_deadline` always mirrors the legacy `HARD_TIMEOUT_S` exactly — a turn can never run *longer* than before, only stop earlier/more precisely labeled. **Design bug caught and fixed before shipping:** an early draft shared one signal between `connect` and `first_token`, which would have wrongly aborted normal slow tool-less chats at 15s instead of 48s — caught by a dedicated unit test, fixed via a separate `mark_connected()` call at the top of `worker.py::run_worker()`.
3. **Idempotent retry replay** (`services/chat_reliability.py`): a durable `chat_request_ledger` Mongo collection records every turn's acceptance (`record_started`, BEFORE any provider/tool call) and terminal outcome (`completed`/`cancelled`/`unknown`). `ChatPanel.jsx` now captures `request_id` off the `meta` SSE frame and sends it back as `retry_of_request_id` on the idle-watchdog's auto-retry. A retry whose original attempt already `completed` (non-error) is replayed verbatim from the ledger — **zero new LLM call, zero new tool dispatch** — closing the "Retry resends a fresh turn, duplicating side effects" gap. A **self-healing reconciler cron** (`chat_ledger_reconciler_loop`, 60s interval, wired into `main.py` lifespan) marks rows stuck at `started` past 240s as `unknown` (never `completed`/`cancelled` — an honest "don't know" that `lookup_retry()` refuses to replay) — added after live testing found real client-disconnect-while-waiting scenarios don't reliably raise `CancelledError` in this ASGI/uvicorn environment.
4. **stream_state enum** (`started`/`streaming`/`complete`/`cancelled`/`unknown`) added to `_persist_turn()` as an optional, additive field alongside the pre-existing `interrupted` boolean (zero behavior change for callers that don't pass it).

**Live E2E proof (real backend, real Mongo, real LLM — MOCK_LLM=false, no mocking):**
- Real chat turn completed ("PONG" reply, `request_id=c9a4bbdc1dab4d8098abee34e7610898`, ~3s real LLM latency) → retry with that `request_id` replayed in **206ms** with `replayed_from_retry:true`, identical content, backend log confirmed "replayed from ledger (no new provider/tool call)", Mongo row directly inspected (`status:"completed"`).
- Client-kill-mid-request repro (killed curl 0.4s into a real `/chat/stream` call): `chat_request_ledger` row durably written `status:"started"` **before** any provider work, confirmed present unchanged for 4+ minutes (proves "task accepted, not silently lost" even across a real dropped connection) — then, once the new reconciler cron's first 60s sweep ran, transitioned to `status:"unknown"` exactly as designed (confirmed via backend log line `chat_reliability reconciler: request_id=... marked 'unknown'`).
- Cross-tenant isolation (#9) and tool-failure-no-false-success (#7): pre-existing coverage, re-verified passing unchanged (`test_isolation_exploit.py` + `test_data_isolation.py` = 23/23; `test_2026_02_required_tool_gate_b3.py` + `test_fake_tool_call_narration_guard_2026_09_09.py` = 55/55).
- New unit/integration suites: `test_timeout_clocks_p1_2026_09.py` (13/13, pure unit, no I/O), `test_chat_reliability_idempotent_retry_2026_09.py` (12/12, real Mongo test db).
- Full targeted regression re-run with the flag now ON: 300+ pre-existing chat/auth/isolation tests, **zero new regressions** — 2 real issues found and fixed during implementation itself before shipping: (a) a catastrophic-regex-backtracking hang in `test_iter136_hard_timeout_enforced.py` caused by the new watchdog condition shape, (b) the connect/first_token signal-sharing design bug above. A handful of pre-existing, unrelated baseline failures (confirmed via `git stash` to fail identically before this round) were left untouched, per instruction to distinguish baseline failures from new regressions.
- Independent `testing_agent_v3_fork` pass: confirmed all of the above via its own live curl verification with different `request_id`s, flagged the same open reconciliation question main agent had already found — closed same-day via the reconciler cron above, re-verified live post-fix.

**Verification labels (per the blueprint's own evidence-rules):** all of the above is `SANDBOX-REPRODUCED` / preview-verified against real infrastructure (real Mongo, real LLM calls, real running backend) — **not** `PRODUCTION-VERIFIED` (no production access in this environment). `/chat/send` (non-streaming fallback) intentionally did NOT receive this round's treatment — documented as out-of-scope since `ChatPanel.jsx`'s live path uses `/chat/stream`. `/ora` (separate advisor subsystem, `ora_chat_v2`) is a different endpoint entirely and was not touched.

## Prior round (2026-09-22) — Admin Overview audit vs codebase

User request (Hindi/English): audit `https://auremcto.com/admin/overview` against the actual codebase, fix any issues found so every feature on the page works, and update PRD/markdown docs. No production credentials are available to this agent, so verification was done against preview (`frontend/src/pages/AdminOverview.jsx` + its ~27 backend endpoints), which is the same codebase that ships to production.

**Two real bugs found and fixed (frontend-only, no backend/data changes):**
1. GitHub Connect Funnel conversion % could exceed 100% — capped at 100%, matching backend's own `pct_num()` convention.
2. Static "System mapping" architecture card was badly stale (claimed "24 pages" vs actual 71+) — rewritten to match verified current repo.

**Testing:** `testing_agent_v3_fork` full regression pass — 0 action items. Report: `test_reports/iteration_admin_overview_regression_2026_01.json`.

## Prior round (2026-09-22) — Interaction reliability audit (implemented, activated 2026-09-23 — see above)

Deep UI/interaction reliability audit across eight shared failure mechanisms produced the `INTERACTION_CONTRACT_V1` machinery, left OFF pending activation. **Activated and P1-gap-closed in the 2026-09-23 round above.** Full detail preserved in `CHANGELOG.md` and `memory/INTERACTION_RELIABILITY_AUDIT_2026_09_22.md` / `memory/CODING_AGENT_BLUEPRINT_V2.md`.

## Known open items (not fixed by any round above)

- `input_normalizer.py` silently changes a code-fenced JS string `a—b` into `a--b`.
- Semantic classifier timeout with prior mode E returns E for a status query.
- Separate advisor engine (`ora_chat_v2`) does not share the main chat's v1 contract, nor the new reliability ledger/clocks.
- `/chat/send` (non-streaming) does not have the retry-ledger/4-clock treatment — `retry_of_request_id` field exists on `ChatBody` but is not wired into `turn.py`'s handler (documented out-of-scope this round; `/chat/stream` is the live production path).
- Production/external blockers: G10 Resend account/domain config (HTTP 400), G8 GitHub sync 401 (expired `GITHUB_ACTIONS_TOKEN`), G15 ingestion evidence, Visibility Day14 actual citation check, E2B configuration, jscpd/churn runtime tooling.

## Next priorities

- **P0:** Rotate/renew `GITHUB_ACTIONS_TOKEN` and fix Resend sender/domain config — both founder/ops actions, not code fixes.
- **P1:** Wire `retry_of_request_id`/ledger into `/chat/send` for parity if that path becomes production-relevant again; production-live verification of the reliability round above once founder has production access available to this agent.
- **P2:** `AdminOverview.jsx` file split into per-card components (~2650 lines, over guideline); calibrated router/model benchmarks; proof-details drawer.

See ROADMAP.md for historical backlog; CHANGELOG.md for prior dated work.
