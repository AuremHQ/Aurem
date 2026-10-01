# Checkup-first experience — Step 0: inventory and decisions

Read-only investigation of `origin/main` @ 790498f. No feature code. Everything below was read
from the repo; "not verified" marks what I did not run.

## 1. The finding that shapes everything: issues live in FOUR places today

| Source | Where | Who reads it | What it holds |
|---|---|---|---|
| `first_scan_results` | `services/onboarding_first_scan.py`, `routers/onboarding_first_scan.py` | `FirstScanCard` | **SEO** first-scan cards (runs `run_seo_fixes`), `findings_count` |
| `cto_open_findings` | writers: `loop_full_scan`, `project_onboarding_scan`, `local_tools.save_finding`; readers: `routers/findings.py`, `advisor_context`, `finding_fix_applier`, `mode_d_debugger` | notification strip, Prompt Starter / ORA Guide popup, Advisor | **critical/high only** from the 7 SCANNERS (security, performance, code_quality, dependencies, database, bug_hunt, docker) |
| `/codebase-health/scan` result | `routers/codebase_health.py`, `services/codebase_health_core.py` | `CodebaseHealth` page, `SecurityScanDrawer`, `PostTaskScan` | full findings + a 0-100 score (`_score_for_findings`, `round(100*exp(-raw/60))`) |
| `services/health_score.py` | founder cockpit only | `HealthScoreWidget` (AdminCockpit) | AUREM's OWN codebase health, 9 weighted categories — **not** a user's project |

Root cause of "scan says 0 issues, ORA Guide says fix the top issue": the first-scan card counts
SEO findings, the Guide popup reads `cto_open_findings` (security/bug findings). Two sources, two
answers. S2 (one source of truth) is a real fix, not a nicety.

## 2. Backend: reuse / extend / new

| Piece | Decision | Why |
|---|---|---|
| `cto_open_findings` | **EXTEND additively** (new optional fields, wider status enum) behind a `services/fix_items.py` layer | 8+ readers/writers already depend on it; a rename/migration is the main way to break production |
| 7 `SCANNERS` + `repo_text_cache` | **REUSE** as the detection engine | deterministic, already tested, no LLM |
| `_score_for_findings` (exp curve) | **REUSE** as the one score function | users already see it; spec's linear formula would be a 2nd formula |
| `services/health_score.py` | **LEAVE ALONE** | internal founder metric, different thing |
| `onboarding_first_scan` (SEO) | **FOLD IN**: its cards become `fix_items` with category `launch` | removes the 4th source |
| `finding_fix_applier`, `/codebase-health/fix` → cto_task | **REUSE** as the "Fix" action | already goes through the gated pipeline |
| `services/feature_flags.py` (Mongo, `rollout_pct`, tier/user allowlists, 60s cache) | **REUSE** for all 7 flags | canary support already exists |
| `routers/supabase.py` / `supabase_provisioner` | **UNRELATED** | provisions AUREM's own Supabase for Personal Track apps |
| Permission Map (RLS/policy parsing) | **NEW — greenfield** | no RLS/policy analysis exists anywhere (`grep` for rls/policy in scanners: 0 hits) |
| Guardian re-scan on push | **NEW** (webhook currently handles `pull_request` / `workflow_job` only) | |

## 3. Frontend: the 16 components named in S3

| Component | Lines | Imported by | Decision |
|---|---|---|---|
| `FirstScanCard` | 480 | ChatPanel, WorkCard, VisualBugAnalysisCard | merge into checkup/Fix List, remove last |
| `PostTaskScan` | 209 | ChatPanel | keep; make it read `fix_items` |
| `GraphReadyCard`, `IdleStartHelper` | — | — | **do not exist** in this repo (0 matches). Probably Emergent-preview-only |
| `StarterChoiceModal` | 349 | Dashboard | merge into checkup screen |
| `SecurityScanDrawer` | 978 | ChatPanel, CodebaseHealth | keep (CodebaseHealth uses it); stop it owning issue state |
| `LoopLiveFeed` | 947 | 17 files | **do not touch** (too widely used) |
| `LiveStepFloatingCard` | 206 | ChatPanel | fold into the single "quiet activity line" |
| `PlanApprovalCard` | 114 | 6 files | keep (it is a real action card) |
| `OperationHistory` | 641 | 6 files | keep |
| `OraGuideMascot` | 610 | 7 files (incl. cookie banner, wizard, App) | **hide under flag first; delete only after the flag has been ON in prod for a while** (Delete Gate needs a written verdict) |
| Advisor side tab (`AskAdvisorReal`) | 514 | 5 files | same: hide under flag, delete later |
| `HealthScoreWidget` | 367 | AdminCockpit only | unrelated (admin) |
| `VisibilityKitPanel` | 426 | 2 files | reuse as the "launch" fix pack UI |
| `PromptStarterPanel` | 212 | OraGuideMascot, ChatPanel | becomes "next step" buttons fed by `fix_items` |

Size warning: `ChatPanel.jsx` is **6,768 lines**, `MessageBubble.jsx` 1,689, `Dashboard.jsx` 854.
Part 6 (chat cleanup) must be a strangler (new flag-gated render path), not an edit-in-place.

## 4. Gaps found (some are existing production issues)

1. **S7 is not true today.** The keep-ratio / syntax / lint gates run only in the `cto_task` workers
   (`worker_api`, `worker_git`). ORA's chat write path `local_tools.write_repo_file` runs a Vanguard
   regex + path validation only; its own docstring says the other layers are "skipped (latency
   budget)". `routers/chat/worker.py`, `routers/mcp.py`, `finding_fix_applier`, `security_scan` also
   commit with none of these gates. Vendored-folder skip lists differ per file and miss `.agent/`,
   `vendor/`, `benchmarks/`.
2. **S6 (branch + PR) is not how chat edits work today.** `write_repo_file` commits to the project's
   configured branch directly.
3. **Raw markdown bug (6.1) root cause (corrected):** `RenderedMessage.jsx` already parses
   `**bold**`, `_italic_`, code, links, images and tables (earlier version of this note was wrong).
   What fell through as literal text was headings (`## x`), list items (`- x`, `* x`, `1. x`) and
   `__bold__`. Fixed in #207 as React elements (no `dangerouslySetInnerHTML`). `MessageBubble`'s
   raw `{m.content}` branch is the user's own message, which is correct to leave plain.
4. **Funnel "sent first message = 0" bug (root cause found):** only `/chat/send` stamped `first_chat_sent`;
   the real send path `/chat/stream` never did. Fixed separately (fix-first-chat-funnel).
5. **Target stacks:** Permission Map is Supabase-first. Lovable/Bolt use Supabase; Emergent and many
   Replit apps are FastAPI+Mongo, where RLS does not exist. For those users the map would show
   "not available" (S9) — half the target audience may see nothing from Part 4.

## 5. Decisions needed (with my recommendation)

- **D1 data model:** extend `cto_open_findings` additively (recommended) vs new `fix_items`.
- **D2 score:** reuse the exp-curve `_score_for_findings` (recommended) vs spec's linear formula.
- **D3 first-scan:** fold SEO first-scan into the checkup as category `launch` (recommended).
- **D4 S7/S6:** add S7 guards to ALL commit paths in **shadow mode first** (log what would be
  blocked for a while, then enforce). Leave the direct-commit chat flow as is; PR-only applies to the
  new fix loop only (recommended). Enforcing on the chat path changes live behaviour (S13).
- **D5 chat cleanup:** strangler under `chat_clean_ui`; ship the markdown fix first as its own PR.
- **D6 floating helpers:** hide under flag now, delete later (recommended).
- **D7 stacks:** which stacks did the real signups use? Decides whether Part 4 needs a Mongo/API-auth
  variant before it is worth shipping.

## 6. Production-safety rules for the whole programme

- Additive schema only: no rename, no migration, no removal in the first release.
- Every new flag default OFF; use `rollout_pct` canary before 100%.
- Every new endpoint fails safe: an error never blocks chat ("Open chat anyway").
- Guards ship in shadow mode (log only) before they enforce.
- No edits to ChatPanel logic; new UI is a separate, flag-gated component.
- Backward-compat tests for each existing reader of `cto_open_findings`.
- I cannot verify production from this sandbox (`docs/environments.md`: prod unverified). "Never
  breaks" is not something I can promise; this list only shrinks the blast radius.

## 7. Revised PR order

0. Markdown rendering fix (RenderedMessage + MessageBubble branch), behavioural + XSS tests.
1. `services/fix_items.py` over `cto_open_findings` + one score function + events.
2. Deterministic checks (runs/safe/launch) + planted and clean fixtures.
3. Permission Map code layer + matrix UI.
4. Checkup screen + pinned bar + Fix List (reads only `fix_items`).
5. S7 guards, shadow mode, all commit paths.
6. Chat clean UI (strangler, flagged).
Then Batch 2 and 3.
