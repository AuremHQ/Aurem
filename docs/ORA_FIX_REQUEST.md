# ORA task-execution and reporting reliability

User report date: 2026-09-23. Implementation started: 2026-09-26.

## Approval boundary

- Implement **Issue 3 only**, in this checkout, with isolated tests and local documentation.
- Pause for the user's independent verification before moving to Issue 6 or later fixes.
- No real GitHub test commits, default-branch changes, production actions, remote documentation writes or provider setting changes authorized.
- User independently exercises the real ORA/GitHub acceptance flow.
- Evidence referenced by user: `docs/ORA_BUG_RETEST_LOG.md` in `AuremHQ/secure-team-workspace`. That remote document has not been fetched or modified in this implementation.

## Ordered backlog and acceptance

| Order | Issue | Required correction | Independent acceptance | Status |
|---|---|---|---|---|
| 1 | 3 — lost confirmation / false success | Stream event + final/history fields, shared saved-proposal resume for button and typed phrase, saved scope/mode, visible failures, commit/task receipts | On an authorized test repo, send a real code-change request and confirm via button; inspect actual GitHub commit. Repeat in a separate proposal using typed `Confirm and proceed`. Both must commit in the same turn; narrative is not proof. | Implemented in this checkout; isolated tests passed; USER LIVE ACCEPTANCE PENDING |
| 2 | 6 — admin approval not evidenced | Truthful logged-only vs executed labels, result record ID, atomic execution claim | Open the named admin destination and see the actual result record | Not started; awaiting Issue 3 verification |
| 3 | 2 — analysis replaced by generic scan | Read-and-answer routing for specific-file analytical questions | Ask about a real file without forbidding the scanner; verify file-content evidence | Not started |
| 4 | 4 — multiline fragmentation | Plain Enter inserts newline; explicit send submits one intact message | Type three lines, click send once; server receives all three lines | Not started |
| 5 | 1 — repo connection/prompt durability | Investigate new project handoff and false relink prompts on already-working projects | New repo appears after confirmed creation; existing valid repo repeatedly opens without false relink | Previous targeted handoff patch exists; new durability report not investigated in this change |
| 6 | 5 — missing approval button dead end | Reproduce with originating proposal/diff context after earlier fixes | No contextless dead-end; recovery retains original proposal | Not started |

## Issue 3 implementation contract

1. A proposal is durably saved before its control is offered. Snapshot: user, session, project, repo owner/name, branch, installation, original prompt and mode.
2. `scope_confirmation` passes through SSE; final response, retry ledger and persisted history retain the proposal identity/prompt.
3. Both button and typed phrase enter the same `/chat/stream` resume branch BEFORE generic routing/context enrichment.
4. Server resolves saved scope and rejects mismatched/expired/cancelled/ambiguous/read-only proposals. It never treats submitted text as a new code-change request.
5. Atomic Mongo claim allows at most one task dispatch. Unknown outcomes never automatically re-execute.
6. Reuse existing task submission ownership, repository access, ambiguity and budget checks. Await the existing task worker in the same stream with a bounded deadline.
7. Success requires a terminal task with a full commit SHA and push/readback evidence; worker/model prose is not evidence. A commit receipt is write evidence, not a claim of correctness or production verification.
8. UI waits for a receipt, shows errors/unknown state truthfully, and never optimistically says “Confirmed — proceeding”.

## Verification status

Temporary Mongo and component tests use **MOCKED GitHub/LLM/task worker dependencies**; no real commit is created by these tests. User's GitHub acceptance remains separate and mandatory.

- 2026-09-26: **28 backend tests passed** (22 new isolated scope-resume cases + 6 existing stream goldens), **13 frontend tests passed** (10 card + 3 stream/history).
- Real temporary Mongo covers proposal → existing authenticated task-submission function → MOCKED worker → durable task/full-SHA receipt; correct session/project, race/idempotency, quota, expiry/cancel, altered scope, unknown/deadline/disconnect and late-receipt reconciliation. Temporary databases dropped.
- External-preview browser with current compiled UI and ALL API calls intercepted: button and typed phrase both dispatch correctly; original card updates from typed receipt; HTTP failure and missing receipt show errors; full SHA wraps at 320/768/1024/1440px. Eight chat payloads inspected. **All execution/provider/API responses in this browser check were MOCKED.**
- First browser attempts were inconclusive (preview development asset 429s and clicking a temporarily disabled control). Follow-up used compiled assets without changing the running service/configuration and explicitly waited for enabled controls. No claim that underlying preview ingress limits were changed or fixed.
- `yarn build`, focused frontend ESLint, new backend module/test Ruff and diff whitespace checks passed. Existing unrelated router lint debt is not declared clean.
- Reports: `test_reports/iteration_393.json` and `test_reports/iteration_393_followup.json`.

## User acceptance checklist — pending, do not replace with assistant narration

Use an authorized non-production test branch with these application changes available:

1. Submit a small, explicit code-change request and obtain a scope proposal showing the intended repo/branch.
2. Confirm using its button; record proposal ID, task ID, full commit SHA, resulting branch and timestamp.
3. Independently open the repository commits page and inspect the actual file diff. The commit must correspond to the saved scope and task.
4. Start a separate request/proposal, then type `Confirm and proceed`. Repeat the independent GitHub check.
5. Refresh the conversation: the receipt should remain visible. Repeated confirmation must not create a second commit for the same proposal.
6. Try cancel/expiry or a deliberately rejected scoped request: it must show a visible failure and must not claim a commit.

Legacy pending proposals created before repository snapshots were introduced are rejected with a new-proposal message. Unknown/disconnected executions are not automatically retried; inspect their task/commit evidence first.

**Stop point:** do not start Issue 6 until the user independently verifies Issue 3 and authorizes proceeding.