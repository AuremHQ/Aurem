# R10 rollback — real GitHub E2E (2026-09-07)

Repo: polarisbuiltinc-wq/aurem-rollback-testbed (disposable). Installation 152797252 verified
via App API to include this repo (see ../R10-e2e-install-verify.log). Preview UI, test@aurem.dev.

| Step | Evidence |
|---|---|
| main HEAD before ship | eca279371e56f017464dab787ffdd43d7b3cc823 |
| ship commit on auremcto/ship-r10e2e-1788751715 (pre-merge head) | 94782191944c1d050129b0b38dfea11864ceb998 |
| PR #1 opened (draft, label aura:ship) → marked ready → squash-merged | merge_commit_sha d82c23671996fe6d14614983950fc9ad9b7d44fe |
| signed pull_request webhook → loop_outcomes | pr_status=merged, merged_sha=d82c2367… (≠ commit_sha 9478219…) |
| UI: Ship history → Rollback → confirm | screenshots captured in-chat (2026-09-07 03:29 run): ship row with ROLLBACK button; themed confirm modal |
| UI terminal label | "Rolled back ✓ (03:29 AM) · 7 steps, all passed" — in-chat screenshot |
| loop_sessions | rollback_commit_sha=d82c2367… (merged sha), rollback_status=done, rollback_verified=True, rollback_sha=1fe24ee |
| GitHub main after rollback | HEAD 1fe24ee3faaf2de6785a2f33e7c6bae9f15dfec4 = revert commit; d82c2367 still in history (no rewrite); shipped file → 404 |

Live named checks: t_ship_sha_captured_on_merge PASS · t_rollback_reverts_merged_sha PASS
(unit: t_rollback_fail_if_revert_missing, t_cancelled_deletes_branch, t_branch_delete_only_aurem_prefix — all PASS in tests/test_r10_rollback_merged_sha_2026_09_07.py)

**R9 rollback = VERIFIED.**
