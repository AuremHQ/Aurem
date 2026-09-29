# QA Report — commit 55ef1dd (2026-09-29T07:26:57+00:00)

**Commit message**: Fix invalid UTF-8 in a test title and stop the style gate crashing on bad bytes

TeamManagementPanel.phase1.test.jsx held a Windows-1252 em dash (0x97), which
crashed ci_check_test_style.py (UnicodeDecodeError) for any PR whose diff
touches that file, failing 'Static-grep threshold on new tests'.

C
**Scope decided**: backend=False, ui=True, scenarios=[]
**Reasoning**: backend=False, ui=True, files_matched=[], keywords_matched=['crash', 'auth']

## Results
| Scenario | Variant | Result | Detail |
|---|---|---|---|
| regression_locks | regression-20260728-ship-gate-infinite-loop | OPEN | bug still open — not gating. test_iter332_ship_gate_skip.py:FAIL |
| regression_locks | regression-20260729-null-turn-prompt-mode-crash | DEFERRED | live-server lock — runs in preview env, not runnable on a CI runner |
| regression_locks | regression-20260729-freetier-499-unboundlocal | PASS | test_regression_iter339m_freetier_unboundlocal.py:PASS |
| regression_locks | regression-20260729-rollback-button-dead-click | PASS | LoopLiveFeed.iter339j_rollback_remount.test.jsx:PASS; LoopLiveFeed.iter329_task2_shipped_row.test.jsx:PASS |

## Regressions checked against
- `regression-20260728-ship-gate-infinite-loop` — status=open (fixed_in_commit=None) — Skip-this-step at ship-approval gate caused infinite Execute->Ship-gate cycle instead of terminating (observed: 3 cycles, 7m22s stall, manua
- `regression-20260729-null-turn-prompt-mode-crash` — status=fixed (fixed_in_commit=iter339k (prod build m1c60458, 2026-07-29)) — PROD P0: prompt-mode chat crashed with "'NoneType' object has no attribute 'get'" after 'ORA recalled...' when chat_sessions.turns contained
- `regression-20260729-freetier-499-unboundlocal` — status=fixed (fixed_in_commit=iter339m (prod build m1c60458, 2026-07-29)) — PROD P0: /chat/stream returned 499 for ALL free-tier/non-admin accounts. A function-local `from services.usage import is_founder_email` insi
- `regression-20260729-rollback-button-dead-click` — status=fixed (fixed_in_commit=iter342 (2026-07-29)) — PROD P0 (3rd recurrence): Rollback button on shipped loops produced ZERO network calls. Root cause class: multi-click confirm flows relying 

## Overall: PASS
