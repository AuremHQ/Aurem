# Admin Team — STAGE 1 / AT1a (route registry + one shared gate, legacy mode)

Status: **GATE MET**

Commit: `0c552a95` — "Admin Team S1 (AT1a): route registry + one shared
request-aware admin gate, legacy mode (no behaviour change)"

## What changed

- `backend/services/admin_access_registry.py` (new) — maps every
  admin-gated `(HTTP method, path)` to a `(screen_key, level)` pair.
  336 entries, covering all 329 real admin-gated routes the fitness
  test found. Routes with no grantable key (owner-only) are encoded
  with `screen_key=None`.
- `backend/cto_services/auth.py` — `require_admin`/`require_admin_dep`
  became request-aware; in S1 this only RECORDED what the future rule
  would refuse (to `db.admin_gate_would_refuse_log`), with zero effect
  on the actual decision. This recording code was later removed in S2
  once real enforcement replaced it — see STAGE-2.md.
- The 4 other independent admin-check definitions
  (`routers/_admin_common.py`, `admin.py`, `mfa.py`,
  `admin_vanguard.py`) were made to delegate to the one canonical gate
  instead of re-implementing the same `is_admin`/`tier=="founder"`
  check a second (or third) time.
- `routers/self_bugs_admin.py`, `admin_gateway.py` (10 call sites,
  pre-approved by the user) — threaded `request` through so these
  explicit inline callers could also record would-refuse data. `request`
  was given a `None` default so direct-call unit tests
  (`test_self_bugs_admin_dashboard_2026_08_31.py`) kept working
  unmodified.

## Evidence

- New tests: `test_regression_iter_adminteam_s1_fitness.py` (registry
  completeness — initially found 106 unregistered routes, fixed, now
  passes with 0 missing) and a would-refuse recording test (later
  retired in S2 once the behaviour it tested was superseded by real
  enforcement).
- Targeted git-stash A/B comparison on every test file that could
  plausibly reference the 7 changed modules (`cto_services/auth.py`,
  `routers/_admin_common.py`, `admin.py`, `admin_gateway.py`,
  `admin_vanguard.py`, `mfa.py`, `self_bugs_admin.py`) found exactly 2
  real regressions (both fixed before this commit): a
  `unittest.mock.patch` target that stopped existing in
  `_admin_common.py`, and two files whose direct-call unit tests broke
  when `request` became a required parameter. Every other failure in
  that candidate set reproduced identically against the unmodified
  code (pre-existing, unrelated to this work).
- Full-suite before/after (S0-only vs S0+S1, `-n 6`): 409 failed / 7835
  passed / 109 skipped / 24 errors -> 385 failed / 7855 passed / 113
  skipped / 24 errors — net improvement, well within this suite's
  observed run-to-run flakiness.
- Preview curl as Owner (`test@aurem.dev`) — `GET /admin/users` -> 200,
  and zero rows written to `admin_gate_would_refuse_log` for that
  request (confirmed by direct Mongo query), proving S1 made no
  behaviour change for the real Owner.
- `/api/health` build hash after restart: `0f7c86a`.

## Next stage

S2 (AT1b) — turn on real enforcement (founder allow-list only, team
membership, MFA session claim, developer role).
