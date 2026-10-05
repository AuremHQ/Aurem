# Admin Team — Overnight Loop State

Resume point if context resets: read this file, then the latest
`STAGE-<n>.md`, then continue from "NEXT STEP" below.

## Progress
- [x] BASELINE (frontend) — complete, see STAGE-0.md
- [~] BASELINE (backend) — re-running in background (was lost once to an
      environment restart ~00:16; this is the 2nd attempt). Log:
      `/tmp/baseline_backend_full.log`. 923 test files, `-n 8` (xdist).
- [x] S0 (FIX 3 — users allowlist) — code + lock test done, curl proof on
      Preview done. NOT yet committed as a separate stage-tagged commit
      (bundled in commit `6dbec14d`, already on `main`). STAGE-0.md: written.
- [~] S1 (AT1a) — IN PROGRESS.
  - [x] Registry file `backend/services/admin_access_registry.py` (336
        entries) — built from PART-2 + Q7 gaps, then corrected against the
        REAL app routes via the fitness test (see below) — found 336 real
        admin-gated (method,path) pairs, substantially more than the ~178
        Q7 estimate (automated sweep beats manual estimation).
  - [x] Fitness test `backend/tests/test_regression_iter_adminteam_s1_fitness.py`
        — PASSES, 0 missing routes.
  - [x] Widened gate in `cto_services/auth.py::require_admin`/`require_admin_dep`
        — takes optional `request`, records D1-only would-refuse to
        `db.admin_gate_would_refuse_log`, NO behaviour change to the return
        value/exception.
  - [x] 4 duplicate definitions now delegate: `_admin_common.py`, `admin.py`,
        `mfa.py`, `admin_vanguard.py` (admin_bin.py already delegated).
  - [x] 10 call sites in self_bugs_admin.py (3) + admin_gateway.py (7) now
        pass `request` through.
  - [ ] NEXT STEP: write the would-refuse lock test (owner session = empty
        record; a tier-founder/is_admin-only session on Preview records a
        would-refuse row), get curl proof, confirm BASELINE rule once the
        backend baseline finishes, write STAGE-1.md, commit, start S2.
- [ ] S2 (AT1b) — not started
- [ ] S3 (AT2) — not started

## Known environment risk
Around 00:16 (container-relative clock) the whole sandbox appears to have
restarted — `/tmp` was wiped and the background baseline pytest process
was killed silently with no error, mid-run. All files under `/app` (git
working tree) were unaffected. Lesson applied: write progress to
`/app/docs/admin-team/*.md` BEFORE starting another long background run,
not after.

## Commits so far
- `6dbec14d` — S0 (FIX 3 allowlist) + pytest-xdist in requirements.txt
- (S1 commit pending — will follow this file's update)
