# Aurem / ORA — Product Requirements and Current Handoff

Last updated: 2026-09-26. The previous 9,681-line combined history is preserved without deletion in `PRD_ARCHIVE_2026_09_22.md`. Historical claims there are not fresh production verification.

## Current task — Issue 3 implementation; PAUSED FOR USER ACCEPTANCE (2026-09-26)

- User explicitly approved implementing **Issue 3 only** in this checkout, isolated testing/local documentation, then pausing for independent verification. No real GitHub commits, production actions or remote-repo documentation writes authorized. Remaining priority: #6 → #2 → #4 → #1 → #5, only after acceptance/approval.
- Built shared saved-proposal resolution BEFORE normal chat intent/context routing for both button ID and typed `Confirm and proceed`. Original prompt/mode and user/session/project/repo/branch/installation snapshot are preserved; changed/expired/cancelled/ambiguous/read-only scopes rejected. Durable atomic claim dispatches at most once through existing task submission ownership/access/budget/ambiguity checks and awaits the existing worker in the same bounded SSE turn.
- Fixed initial scope-event forwarding, final/retry/history fields, frontend callback await/failure handling, receipt refresh and typed-confirmation update of the original card. Scope consent gate moved before mode-specific cascade. Task workers retain full commit SHA; success requires actual terminal task push/readback evidence, not generated text. Timeout/disconnect/uncertain writes remain unknown and are not automatically retried. Cancellation reports success only when its atomic update wins.
- New modules: `backend/services/scope_resume.py`, `backend/routers/chat/scope_resume.py`. Existing integrations/database stack unchanged; runtime APIs are NOT mocked. Tests: `backend/tests/test_scope_resume_issue3.py`, `frontend/src/components/__tests__/ScopeConfirmCard.issue3.test.jsx`, `frontend/src/lib/__tests__/scopeConfirmation.issue3.test.js`.
- **Evidence:** 28 backend + 13 frontend tests passed (41). Temporary REAL Mongo records with MOCKED provider/task worker/auth/context dependencies. Current compiled UI tested in external browser with ALL API/execution responses MOCKED: button + typed payloads, callback failure, missing receipt, original-card update and responsive receipt wrapping passed. Initial independent browser run was inconclusive; self-test follow-up resolves fixture/control timing gaps without changing platform settings. Build/focused lint passed; no whole-repository lint-clean claim.
- Reports: `test_reports/iteration_393.json`, `test_reports/iteration_393_followup.json`. Request, scope and user acceptance checklist: `docs/ORA_FIX_REQUEST.md`.
- **P0 next:** user runs real code-change confirmations (button and typed in separate proposals), independently inspects GitHub full SHA/diff/branch/task record, then approves moving to Issue 6. REAL GITHUB ACCEPTANCE AND PRODUCTION OUTCOME REMAIN UNVERIFIED. Older proposals without scope snapshots require a new proposal. No accounts/passwords changed.

## Prior diagnosis — false-success incidents #3 and #6 (2026-09-23)

User supplied six task-handling bugs with priority 3+6 → 2 → 1+4 → 5. Latest scope explicitly narrowed to **read-only diagnosis and isolated reproductions for #3/#6; NO implementation, real commits or production actions without separate approval**. Application source/config unchanged in this diagnostic round; only diagnostic artifacts/memory written.

- **#3 confirmed transport gap:** `worker.py` emits `scope_confirmation`; `watchdog.py::consume_worker_queue` drops it; `stream.py` done payload omits scope fields; ChatPanel provides no `onScopeConfirmation` handler and reads missing done fields. Earlier helper-level scope-confirm tests did NOT prove this complete flow works; consider scope-confirmation integration incomplete, not production-verified.
- **#3 confirmed typed-text path:** exact `Confirm and proceed` → heuristic CASUAL (0.86), pending_action `new_request`, confirmation predicate false, even with `pending_fix=True`; isolated pre-LLM test returns zero-tool casual result. IMPORTANT: actual ScopeConfirmCard button resends original prompt + proposal ID (serialization IS wired); typed-phrase reproduction alone does not prove the user's exact button incident.
- **#3 additional gaps:** confirmation ID only consumed when a newly computed `needs_confirm` is true; stored execution mode is not restored; proposal confirmation API does not validate expected session/project. ScopeConfirmCard dynamically reproduced showing proceeding despite callback returning `{ok:false}`.
- **#6 confirmed semantic/UI gap:** catalog `create_backlog_item` writes `ora_backlog_items`, not `cto_tasks`, `ora_self_bugs`, or `cto_founder_suggestions`; no repair/code-commit executor is in this catalog. A missing row in those other panels does NOT prove no backlog record exists. OraChatDrawer dynamically shows `Approved and executed` for `{}` and `{ok:null}` because it accepts anything except explicit false, and discards action result identifiers.
- **#6 additional race:** approve route has no atomic claim. Isolated concurrent stale-proposal-read harness invokes mocked executor twice; not evidence that this happened in the reported production incident.
- **Evidence:** `test_reports/iteration_392.json`, `test_reports/iteration_392_followup.json`; harnesses only in `/tmp`. Real functions tested with in-memory/MOCKED dependencies; two additional actual React component tests passed. Source-only findings and dynamic proofs are distinguished. No matching `AuremHQ/secure-team-workspace` project in preview DB; original production session/proposal/action IDs unavailable.
- **Next:** present/approve targeted fix plan; implementation remains unapproved. Remaining issues #2/#1/#4/#5 not newly investigated end-to-end in this round. Original GitHub fix's live-provider/production verification remains pending.

## Latest task — GitHub connect to Projects handoff (2026-09-23)

User reported: successful GitHub repository authorization from `/projects` > Add Project never appears in the left project list, including after reload. Approved targeted diagnosis/fix with existing permissions preserved; no production/GitHub settings changes.

- **Reproduced before fixing:** callback confirmation did not finish popup polling; equal-count repository replacement was invisible; background sync stopped on an unrelated installation's global connected state; no-opener bridge returned to Dashboard where existing users lack an onboarding wizard. Refreshed AddProject wizard also had no explicit recovery for previously authorized repositories. GitHub authorization is an access grant, NOT automatic project creation; no import-all behavior added.
- **Implemented:** per-installation repo signatures and bounded synchronization; origin/source-validated popup completion with stale-attempt guards; `/github/app/installed` returns to `/projects?flow=connect-repo…`; Projects mounts the wizard for this return and awaits the saved project's list refresh/selection. Explicit already-authorized repo recovery, actual default branch, inline creation/refresh errors, existing-project (409) recovery, and duplicate-save guard. Mobile wizard uses a body portal, shared modal layer and focus trap above the floating guide.
- **Architecture unchanged:** React/Vite + FastAPI + MongoDB; existing installation ownership and repository checks retained. Existing `/cto/projects/add` and `/cto/projects/list` persist/list records; no new database/auth/provider introduced. No live credentials/settings were changed and no runtime APIs mocked.
- **Verification:** 38 frontend + 57 backend targeted tests pass (95 total); backend includes REAL temporary Mongo callback → explicit create → list persistence, duplicate prevention and foreign-owner rejection. Temporary DBs cleaned. GitHub provider/scans and integration-test identity resolver are **MOCKED**. Independent report `test_reports/iteration_391.json`; follow-up `test_reports/iteration_391_followup.json`. Actual preview login and modal handoff/browser checks pass at 320/390/768/1024/1440px. Vite production build and focused frontend lint pass; new Python test files lint clean. Existing GitHub router has 27 unchanged Ruff findings, verified against HEAD (zero introduced).
- **Limit:** no user-controlled REAL GitHub consent/install/save E2E or production validation. No claim this patch has reached or repaired production. Earlier production health/export issues remain unresolved by this task.
- **RCA/reference:** `GITHUB_PROJECT_CONNECT_RCA_2026_09_23.md`. Core files: `frontend/src/hooks/useGitHubConnectStatus.js`, `frontend/src/components/AddProjectWizard.jsx`, `frontend/src/pages/Projects.jsx`, `frontend/src/lib/zIndex.js`, `backend/routers/github_app.py`.

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

## Trust & Verification round (2026-09-23) — six enhancements, audited + built (see CHANGELOG.md for full detail)

User requested an audit against a six-item Trust & Verification spec, then build of everything missing. Audit found Enh.1 (independent verification) and Enh.5 (pre-execution scope confirmation) partially built (Loop Mode only / signal-computed-but-not-gated), Enh.2/3/4/6 fully missing. Built this round:
- **Enh.1** — `services/task_independent_verifier.py` extends the existing Loop-Mode-only independent-verifier pattern to the single-edit chat-task pipeline (`worker_api.py`, `worker_git.py`), gated BEFORE commit/disk-write. Shared append-only ledger `services/verification.py` (`task_verifications` collection) now receives writes from BOTH Loop Mode (`loop_independent_verifier.py`, additive mirror) and single-edit tasks — enforces `verified_by != executed_by` at the write choke point.
- **Enh.2** — `VerificationLevelBadge.jsx` + backend `display_level()`; surfaced in `LiveTaskPopup.jsx` and `TaskProgressCard.jsx`.
- **Enh.3** — `services/task_downgrade.py`: `done -> needs_review` guarded/idempotent transition, explicit `POST /verification/tasks/{id}/report-issue` (+ UI button on `TaskProgressCard`), implicit correction-report regex trigger, `re-verify` endpoint (never a manual flip back to done).
- **Enh.4** — `services/trust_calibration.py` + `trust_calibration_cron.py`: 30-day correction-rate snapshot, `GET /admin/trust-calibration`, "not enough data" (never fabricated 0%) when denominator is 0.
- **Enh.5** — `services/scope_confirmation.py` + `_scope_confirmation_gate()` in `routers/chat/worker.py`: gates the real consequential-execution choke point (right before `_run_orchestrator`) using the classifier's own pre-existing-but-previously-unwired `needs_confirm` signal. `ScopeConfirmCard.jsx` renders Confirm/Change/Cancel.
- **Enh.6** — `backend/scripts/lint_absolute_claims.py` + `absolute_claims_allowlist.json`, wired into `.github/workflows/ci.yml` as a new required job. Found and fixed 1 real violation in `services/first50_campaign.py` ("always works" onboarding email copy); allowlisted 3 genuine false positives.

Tested: 14 new unit tests (`test_trust_verification_2026_09.py`) + 6 new live-endpoint e2e tests (`test_trust_verification_e2e_2026_09.py`), all passing; existing reliability/interaction regression suites unaffected (64/64 + prior suites green); 2 pre-existing failures in `test_cto_worker_safety_pipeline_2026_09_08.py` confirmed via `git stash` to be UNRELATED to this round. **Not live-verified**: a real GitHub-connected task completion through Enh.1/2/3's full lifecycle — preview `test@aurem.dev` only has demo-fixture projects (fake `installation_id`), no real GitHub App installation to exercise `worker_api.py`/`worker_git.py` end-to-end. Enh.5's live SSE trigger also not exercised (depends on classifier's own 0.55 heuristic firing on an ambiguous prompt) — code path + UI verified statically/via unit tests only.

## Known open items (not fixed by any round above)

- `input_normalizer.py` silently changes a code-fenced JS string `a—b` into `a--b`.
- Semantic classifier timeout with prior mode E returns E for a status query.
- Separate advisor engine (`ora_chat_v2`) does not share the main chat's v1 contract, nor the new reliability ledger/clocks.
- `/chat/send` (non-streaming) does not have the retry-ledger/4-clock treatment — `retry_of_request_id` field exists on `ChatBody` but is not wired into `turn.py`'s handler (documented out-of-scope this round; `/chat/stream` is the live production path).
- Production/external blockers: G10 Resend account/domain config (HTTP 400), G8 GitHub sync 401 (expired `GITHUB_ACTIONS_TOKEN`), G15 ingestion evidence, Visibility Day14 actual citation check, E2B configuration, jscpd/churn runtime tooling.

## Next priorities

- **P0 — current bug verification:** complete one live GitHub authorization → selected repo → explicit Add project → selected list entry → reload flow with the correct Aurem/GitHub account. Provider consent is not proven by mocked fixtures. Previously granted repos can be recovered via Add Project → Choose an already-authorized repository.
- **P2 — connect flow:** split oversized project/connect UI modules in a separate change; consider a concise access-granted / repository-selected / project-saved status indicator.
- **P0:** Rotate/renew `GITHUB_ACTIONS_TOKEN` and fix Resend sender/domain config — both founder/ops actions, not code fixes.
- **P0:** Live-verify the Trust & Verification round's Enh.1/2/3 lifecycle against a REAL GitHub-App-installed project (preview account only has demo fixtures) — run a real chat-driven edit task and confirm verification_status/level persist + render correctly end-to-end.
- **P1:** Wire `retry_of_request_id`/ledger into `/chat/send` for parity if that path becomes production-relevant again; production-live verification of the reliability round above once founder has production access available to this agent.
- **P1:** Trigger Enh.5's scope-confirmation gate live with a real ambiguous prompt (classifier's 0.55 threshold) and confirm the ScopeConfirmCard/Confirm-resend loop end-to-end in the browser.
- **P2:** Extend Enh.2's verification-level badge to more historical chat-history surfaces (currently: live popup + persistent ship card; older closed message bubbles don't re-fetch).
- **P2:** `AdminOverview.jsx` file split into per-card components (~2650 lines, over guideline); calibrated router/model benchmarks; proof-details drawer.

See ROADMAP.md for historical backlog; CHANGELOG.md for prior dated work.
