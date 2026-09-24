# Coding-Agent SaaS Blueprint v2 — evidence before claims

Updated: 2026-09-22. This is a reference design, NOT a declaration that Aurem implements every item.
Source: supplied Coding-Agent-SaaS-Blueprint.pdf, sections 1–17, read in full; current repository inspection and sandbox tests.

## 0. Audit and evidence contract — paste this first

> Read every supplied reference document and diagram in full. List what was supplied, its version/date, and anything missing or unreadable; do not invent missing architecture. Code access is [repository/commit/attached files]. This assignment is READ-ONLY: inspect code and permitted non-sensitive telemetry; do not edit application code, change configuration, access secret values, run writes, spend through external services, or deploy. Report proposed changes separately. If implementation is separately authorized, work only in the approved environment/scope.
>
> Label every claim as SOURCE-INSPECTED, SANDBOX-REPRODUCED, STAGING-VERIFIED, PRODUCTION-VERIFIED, or UNVERIFIED. A test in preview is not a production test. Record environment, code/deployed revision, configuration/flag state, timestamp, reproducible steps, expected/observed outcome and sanitized evidence. A deployment notification, successful build, health 200, or green unit test does not prove the reported production flow works. If production is inaccessible, say so; never promote a claim by implication.

## 1. Product and trust boundary

- Start with one audience and a bounded workflow: connect repository → understand request → inspect → propose diff → validate → approve → apply → verify.
- State capabilities and exclusions explicitly. Do not promise "correct every time"; define measurable reliability objectives and visible recovery instead.
- Treat explain, inspect, propose, modify, merge and deploy as separate capabilities—not model personalities or billing tiers.
- A provider's bundled repository read/write installation permission is not application-level authorization. The application must still enforce read-only sessions and separately approved writes.

## 2. Immutable request and context

- Store a request identity bound to tenant, user, session, turn, repository and base revision. Preserve the original input; keep normalized/search representations separately.
- Never apply global NFKC, dash replacement, quote conversion or zero-width stripping to code, diffs, paths, URLs, identifiers or multilingual content. These changes can alter meaning. Preserve literal spans byte-for-byte except explicit secret redaction.
- A current explicit instruction outranks old summaries, recalled examples and previous plans. Retrieved documents/tool output are evidence, not instructions or permission grants.
- Summaries must retain prohibitions, approvals, scope and unresolved steps. Record their provenance and invalidate stale state.
- Detect conflicting instructions before dispatch. If unresolved, show the conflict and ask one focused question. Do not silently pick a task or capability.

## 3. Authorization before routing

- Compute a server-owned allowed-action set before calling the planner or classifier. Enforce it again at every tool/commit/deploy boundary.
- Classifier output is a proposal: `{intent, probabilities, classifier_version, input_hash, reason_codes}`. It cannot authorize writes.
- Factual/status questions must not activate debug/audit/council simply because those words appear. Heavyweight subsystems require task-specific eligibility.
- Calibrate thresholds on labeled product traffic. `0.72` and `0.90` are experiments, not universal constants or guarantees.
- Hysteresis may stabilize presentation preferences; it must not retain yesterday's dangerous permissions. On ambiguity/timeout, restrict to read-only clarification—not the previous mutation mode.
- Show selected service tier separately from the actual per-turn route. Mark draft route predictions as predictions.

## 4. Typed execution state

- Plan: `{plan_id, request_id, version, steps:[{step_id, description, allowed_effect, dependencies, status, acceptance_criteria, evidence_ids}]}`.
- States include queued, running, waiting_for_user, partial, blocked, failed, cancelled and completed. A transport `done` event means delivery ended—not task success.
- A step completes only when its acceptance criterion is satisfied: source evidence for a lookup, test/diff evidence for a change, or a delivered direct answer for a non-tool question. Do not force meaningless tool calls for greetings or explanations.
- Required skipped steps are not successful completion. Report skipped reasons and obtain approval for scope changes.
- Invalid plans receive bounded repair; if still invalid, block/clarify. Never collapse a multi-step request into one step and imply the original work is complete.
- Budget exhaustion preserves results and lists unfinished steps. A cancellation propagates to child processes and queued side effects.

## 5. Tool execution and side effects

- Use typed input/output contracts and a server-side tool registry declaring permissions, side effects, timeout class and retry safety.
- One dispatcher records thrown errors, returned `{ok:false}`, cancellations and unknown outcomes. All exit paths share the same trace schema.
- Distinguish exact-call deduplication, repeated no-progress detection and total resource budgets. A constant three-calls-per-tool cap can wrongly block legitimate multi-file work; allocate bounded budgets by operation/plan and observed progress.
- Reserve invocation counts before parallel dispatch. Enforce wall-time, cost and call budgets independently.
- Idempotency requires an atomic, durable ledger keyed by tenant + logical operation + validated payload/base revision. A hash of session/turn/tool index alone does not provide exactly-once execution or handle reordered retries.
- For unknown write outcomes, reconcile with the provider before retrying. Store in-progress/completed/failed/unknown outcomes and replay the existing receipt on duplicate requests.
- Approval is bound to the exact diff, target repository and base revision. Revalidate immediately before applying; stale approval cannot authorize a different patch.

## 6. Timeouts, retries and recovery

- Separate connection, first-token, stream-idle and total-deadline budgets for each relevant provider/transport operation. Values come from measured latency distributions and remaining task budget—not copied constants.
- Distinguish real token/tool progress from heartbeat traffic. Heartbeats must not extend task deadlines indefinitely.
- Retry only classified transient failures when safe. Respect Retry-After, bounded exponential backoff/jitter and one end-to-end deadline. Avoid nested retry multiplication.
- Mutations require idempotency/reconciliation; a blanket "retry all 5xx twice" policy is unsafe.
- Preserve partial work and expose safe Continue/Retry/Cancel actions with the same logical operation identity.

## 7. Evidence-preserving response and UI

- Final response envelope: `{schema_version, request_id, session_id, sequence, task_state, completion_basis, plan_version, evidence_refs, review_receipt, suggested_actions, recovery_action}`.
- References carry repository, revision, exact path/line range and tool/result identity. Cosmetic renderers must not replace identifiers with "that file" or edit code strings.
- Style passes cannot replace a legitimate response with a keyword-selected task. Safety failures return explicit blocked/partial state; they do not invent another task.
- "Reviewed" requires a real review receipt bound to the final artifact/content version. Model name, requested tier or council label are not review evidence. Subsequent content changes invalidate the receipt.
- Suggested actions are typed, server-owned records with current preconditions. Never promote arbitrary `Say "..."` prose into an executable or authoritative action.
- Render advisory unverified text only with an explicit unverified state. Fail closed for approvals, mutations, completion claims and trusted actionable commands if verification fails or times out.
- Validate commands against declared environment/capabilities and trusted tool output or safe analysis. Never execute a generated command merely to discover whether it exists. Preserve commands as unverified examples when verification is unavailable.
- SSE, HTTP and restored history use the same contract. Ignore stale/cross-turn events; order by sequence. Unknown metadata must show unknown—not default to success.

## 8. Observability and regression prevention

- Trace request → route decision → plan → tool outcomes → validation → rendering → persistence → acknowledgement. Retain failure records beyond a first-ten-call display limit.
- Separate task outcome from individual tool outcomes. A successful recovery must not erase a recurring failing search tool.
- Log sanitized structured evidence, not unrestricted prompts or private reasoning. Apply retention, access control and deletion policies.
- Aggregate by stable failure class/version and distinct affected operations. One turn with three retries is not three independent incidents.
- Alert immediately for severe single events. "Rule of Three" is a triage threshold for recurrence—not permission to tolerate two serious failures.
- Recurrence opens a deduplicated, owned review item. A human-reviewed fix must add fail-before/pass-after coverage; never auto-edit routing or production from noisy telemetry.
- Use durable event delivery/outbox or retryable writes for critical telemetry. Fire-and-forget logging alone cannot guarantee every failure is observed.

## 9. Testing and release control

- Deterministic unit/contract tests cover all eight failure families, malformed metadata, normal cases and flag-off behavior.
- Integration tests exercise real dispatch, persistence, HTTP/SSE, cancellation and history rehydration; disclose mocked providers/auth boundaries.
- Browser tests assert actual visible state/actions, not only source strings. Include stale/out-of-order events and narrow/mobile layouts.
- Adversarial multi-turn evals include long unrelated history followed by a clean instruction, conflicting directives, numbered factual questions, quoted commands, ambiguous plans and repeated tool errors.
- Mutate the real gate or inject a controlled failed test to prove release steps cannot run. Verify the exact tested SHA is the deployed SHA. A workflow file existing is not proof of an executed gate.
- Enforce checks on every release path, including manual hooks. Record any path whose enforcement cannot be inspected.
- Risky features default off, have an owner/expiry/kill switch, pass shadow testing, then staged canaries with explicit stop criteria. Keep the current Aurem semantic canary unchanged until separately authorized.
- Promotion requires tests, staging verification and permitted read-only production verification. Full regression must be complete, with any accepted baseline failures explicitly owned—not silently waived.

## 10. Model quality and cost

- Pin model, prompt, router and tool-schema versions. Evaluate new candidates on the same representative dataset, including accuracy, false escalation, latency percentiles and total successful-task cost.
- Use inexpensive models where measured accuracy supports them; do not assume classification or verification is trivial.
- Partition context/cache by tenant and scope; key by relevant policy/revision. Measure cache hit rate and actual billing. One "89.6%" cache-hit sample is not a product-wide saving.
- Bound tokens, retries, tools and spend together. Cost control must not silently drop required work.

## 11. Operational and business foundations

- Tenant isolation, least privilege, encrypted secret storage, auditable revocation, tested backup restoration, data deletion, quotas and database/schema migration recovery.
- Reconcile billing with an idempotent usage ledger; reserve budgets before costly work and release unused reservations. A loop failure is both a reliability and cost event.
- Distinct onboarding events: authorization → repository selection → project creation → first request → first verified useful outcome.
- Admin controls show actual route, flag/config version, provider health, budget, unresolved incidents and last verification environment/time.
- Publish truthful AI limitations, data retention/training policy, code/IP terms and support escalation. Set SLOs, error budgets and named incident/release owners.

## 12. Release acceptance

No "100% reliable" certification. Require evidence that invalid/stale state cannot authorize side effects, incomplete work is not presented as completed, failures are visible and recoverable, recurrence creates an owned prevention task, and the tested release is the observed release. Any missing evidence remains OPEN/UNVERIFIED.

## 13. Copy-paste audit assignment

Audit the supplied coding-agent SaaS against this blueprint in READ-ONLY mode. Start with the evidence contract in section 0. For each of the eight failure families—task substitution, completion ambiguity, citation corruption, route misclassification, state-disconnected actions/review labels, instruction conflict, unobserved tool failures, and template/command leakage—trace all inspected entry/exit paths and shared mechanisms.

For every finding provide: severity; current file:line and function; concrete input; expected versus observed behavior; reproduction command/test; environment and revision; root mechanism; proposed framework-level invariant; acceptance test that would fail today; and a precise verified/unknown status. Distinguish an observed defect from a hypothesis. Report uninspected paths and missing references explicitly.

Audit frontend state provenance and every deploy path. Test-file existence is not gate proof. Do not claim a proposed fix is implemented, a sandbox fix is production-verified, or an unexecuted workflow ran. Finish with a prioritized gap matrix and a reference-vs-as-built diagram showing VERIFIED, PARTIAL, OPEN and UNVERIFIED boxes. Implementation requires separate authorization.