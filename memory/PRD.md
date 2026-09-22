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

## Current user request and constraints

Deep UI/interaction reliability audit across eight shared failure mechanisms, structural rather than one-instance fixes; file:line and reproducible evidence; fail-before/pass-after regressions in actual release gates; risky behavior OFF by default; no unrelated changes or Docker changes; no production claims without production evidence.

Subsequent request: read supplied **Coding-Agent-SaaS-Blueprint.pdf** completely, refine it using real engineering standards, compare the existing system read-only, provide concise copy-paste wording. Explicit additions: reference/access inventory, read-only safety boundaries, no secret access/deployment, and sandbox/staging/production verification distinction.

## This round: implemented, NOT activated in production

- New opt-in `INTERACTION_CONTRACT_V1=1`: bounded factual/conflict boundary, response fidelity, typed completion envelope persisted/transmitted, metadata-backed UI, neutral bails instead of fabricated tasks.
- Real tool outcomes normalized; loop/normal trace names reconciled; failed-tool signatures beyond ten calls feed recurrence even when final task succeeds. Repeated retries in one turn are not counted as multiple turns.
- New blocking `scripts/interaction_gate.sh`, reusable workflow, explicit needs in two tracked deploy workflows, and common predeploy invocation.
- Frontend malformed-action crash and bounded `Never modify files` conflict variation found during testing were corrected.
- Flag remains absent/OFF; semantic canary unchanged. `review_evidence` is deliberately null and suggested actions empty in v1 until evidence producers exist. Do not describe their omission as full provenance implementation.

## Testing and exact limitations

- Final contract gate: **38 backend + 8 frontend tests passed**. Initial existing-path baseline: backend 11 failures / 7 passes, frontend 3 failures.
- Real ASGI conflict-boundary tests use MOCKED auth resolution/provider spies; tool tests use MOCKED callbacks; Mongo tests are isolated and real. No paid-model replay.
- Full frontend: **11 failed / 795 passed**; the same 11 failure cases occur in an archived baseline run (which has one additional failure). Full backend regression started but stopped around 10%, with failures; no clean full-suite claim.
- Full frontend ESLint and new Python-module/test pyflakes pass; existing ten Python warnings verified unchanged versus baseline.
- Completion checker again reported a JavaScript linter ENGINE error. Independently reran actual `yarn eslint src --max-warnings=9999` after that message: exit 0 (`test_reports/interaction_frontend_lint_final.txt`). This is not a source-lint failure; do not claim the full regression suite is green because lint passed.
- Preview load/health200 verified. No production behavior verified. Remote GitHub workflow run and bypassing release paths not verified; local dependency assertions and real gate failure injection pass.

## Deliverables / source of truth for this round

- `memory/INTERACTION_RELIABILITY_AUDIT_2026_09_22.md`: eight sections, references, reproducible cases, gate coverage, limitations, updated mapping.
- `memory/CODING_AGENT_BLUEPRINT_V2.md`: refined architecture and copy-paste read-only audit assignment.
- Public copies under `frontend/public/audits/`.
- `test_reports/interaction_audit_verified_gate.txt`, `test_reports/interaction/{backend,frontend}.xml`.
- `test_reports/iteration_interaction_audit_baseline.json`, `iteration_interaction_audit_final.json` (latter describes an intermediate failure; final gate log supersedes it).
- `scripts/probe_blueprint_gaps.py` / `test_reports/blueprint_read_only_probes.json`: read-only evidence, NOT acceptance tests.

## Confirmed new read-only design gaps — NOT fixed

- `input_normalizer.py` changes a code-fenced JS string `a—b` into `a--b` without flagging it. Request hashes currently bind normalized, not original raw input.
- Semantic classifier timeout with prior mode E returns E for a status query. Current-mode fallback is not inherently safe; capability restrictions must be independent.
- Three-call tool-NAME limit can block legitimate distinct-file work; not equivalent to signature dedup/no-progress analysis.
- Separate advisor engine and legacy messages do not share full main-chat v1 contract. Full typed plan, evidence-bound receipts/actions, universal capability enforcement, durable events, CLI validation and four-clock/idempotency remain incomplete.

## Prior fixes preserved / pending production work

Earlier fixes: deterministic canned substitution, selected Council/status boundary, severity-aware suggestions, intention-only text marker, G21 success cache, G18 four fetch timeout signals. These are NOT promoted to production-fixed by this audit.

Production/external blockers remain: G10 Resend configuration, valid G20 incident remediation, G15 ingestion evidence, Visibility Day14 actual citation check, E2B configuration, jscpd runtime tooling and churn git-history/CI-ingestion architecture. Follow original constraints; do not suppress guards or fabricate checks.

## Next priorities

- **P0:** full regression completion, staging/browser parity and authorized production/revision verification before activation; approve a separate raw-input fidelity fix; universal read-only capability boundary.
- **P1:** typed plan/completion evidence, alternate-engine adoption, content-bound review/action receipts, durable tool events, stale SSE reconciliation, free-form CLI validation, real long-history replay, search_repo failure/root-cause work.
- **P2:** calibrated router/model benchmarks and canary expansion only with approval; proof-details drawer showing environment/revision/evidence.

See ROADMAP.md for historical backlog; CHANGELOG.md for prior dated work.