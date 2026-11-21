# CI / DevOps runbook — Dec 1 restart

> **Scope:** ORA Codebase Health Score, **DevOps/Infra (0/100, weight 5%)**.
> **Status:** score is 0 and **cannot move before Dec 1**, no matter what code
> lands. This runbook is the exact restart plan for the day GitHub Actions
> minutes reset.

## What the scorer checks (read from `backend/services/health_score.py`)

`score_devops_infra(db)`:

- `ci = _ci_pass_rate_30d()` — a **live** GitHub Actions pull:
  `GET /repos/{GITHUB_REPO}/actions/workflows/ci.yml/runs?per_page=100&event=push`
  (up to 3 pages), last 30 days only.
  `pass_rate = success / total`, success = `conclusion == "success"`.
  - CI unavailable → **UNSCORED**. `total_runs_30d == 0` → **UNSCORED**
    ("No ci.yml push runs in the last 30 days.").
- `rollback = _rollback_penalty(db)`:
  - latest v2 ledger row `db.rollback_attempts` (`result == "success"`) → **0**.
  - latest failed → **30**. No v2 row → legacy `rollback_status` fallback
    (empty → **25**, "zero positive-path rollback evidence"; failed → **30**;
    success → **0**). Lookup failure → **20**.
- Final: `score = pass_rate * 100 − penalty`, clamped to 0–100.

**Why it reads 0 now:** the dimension is *scored* (not unscored), so CI is
reachable and runs exist in the window — and `pass_rate * 100 − penalty ≤ 0`,
consistent with pass_rate ≈ 0% (chronic reds since Nov 8; Actions minutes
exhausted since ~Nov 15, so every run since fails or never starts). The exact
pass rate is only visible in the GitHub Actions UI or the Admin health-score
evidence — there is no repo-side tool that lists workflow runs.

## What this PR closed (before Dec 1, no CI needed)

**2 blocking test failures in `backend/tests/test_supervised_tasks.py`** —
the test fake `_FakeIncidents` lacked `find_one_and_update`, which
production `incident_log.open_incident` has used since the Aug 25 TOCTOU
fix (atomic pipeline upsert). Both tests failed with
`AttributeError: '_FakeIncidents' object has no attribute 'find_one_and_update'`
on unmodified main; neither test is in any quarantine file, so they run in
CI's blocking lane.

Fix (test file only, no production change, no weakening): a faithful
`find_one_and_update` mini pipeline-emulator on the fake — evaluates the
`$ifNull` defaults, the `$add` recurrence bump, and plain values; first
call creates the row, repeat calls bump recurrence + refresh `last_seen`.

Evidence (local, targeted file-level runs — no full-repo checkout exists
on this VM, so a full suite run was impossible):
- before: **2 failed, 4 passed** (pristine main copy)
- after: **6 passed** (`tests/test_supervised_tasks.py`)
- `tests/test_iter386_error_handling_items.py`: **14 passed** (same run)

**Merge note — PR #347 touches the same file:** #347 added a separate
`_FakeIncidentColl` class for its sweeper tests and left this fake
untouched. The two hunks are in different parts of the file; if git
cannot auto-merge, keep **both** changes (the new method here + the new
class there).

## What is still open (not reproducible locally)

**`test_iter386_error_handling_items.py` — 1 failure reported in CI,
not reproduced here.** A sibling agent recorded "1 FAILED pre-existing
(test_iter386, proven on unmodified HEAD)" during a full-backend run.
In this environment the whole file passes **14/14**, including with the
real `tests/conftest.py` and the full import closure
(`routers/payments`, `routers/suggestions`, `routers/supabase` +
`cto_services/*` + `services/{rate_limiter,subscription_tiers,
team_permissions,wallet,billing_cron,supabase_provisioner,financials,
http,retry_guard,checkout_gateway,cron_runner}`). No test function named
`test_iter386_*` exists anywhere else in the repo (code search, 0 hits),
so the label refers to this file. **Do not guess a fix.** The Dec-1
step is to run this file in CI and read the failing nodeid.

## Dec 1 — exact restart steps

1. **Confirm minutes reset** (Actions usage page; 2000/2000 exhausted
   until Dec 1).
2. **Trigger a ci.yml push run** — merge a ready DRAFT PR (this one, or
   #344/#345/#346/#347/#348) or push an empty commit to main. Do **not**
   push anything else first: every push while minutes are exhausted
   triggers another failing run that counts in the 30-day window.
3. **Read the pass rate** in the GitHub Actions UI (workflow "AUREM CI —
   Build + Test Guard", `ci.yml`) and in Admin → Codebase Health Score.
4. **If still red, triage in this order:**
   - `tests/test_iter386_error_handling_items.py` — read the failing
     nodeid (see "still open" above); fix the real cause, never the test.
   - The regression baseline lock (`tests/baseline_counts.txt`,
     locked 2026-08-25: `failed=321, errors=62, passed=5888`) — the
     deploy gate (`scripts/ci_check_regression_baseline.py`) blocks if
     failures exceed it. This baseline is **3 months old**; a fresh
     full-suite run is needed to re-baseline, and re-baselining needs
     explicit human sign-off (never bump the numbers to make a
     regression "pass").
   - `ci.yml` has 8 jobs and **all must pass** for a run to count as
     success: `frontend-build`, `backend-tests` (full pytest `--cov`,
     coverage ratchet guard, regression baseline lock), `simulated-user-qa`
     (needs `EMERGENT_LLM_KEY`), `security-scan` (G15 CVE scan, blocking),
     `secret-scan` (trufflehog, verified secrets only), `absolute-claim-lint`,
     `deploy-gate` (main-only), `delete-gate`.
5. **Rollback checks (Teji, admin UI — no login exists on the VM):**
   - Admin → Rollback → Attempts (`GET /admin/rollback2/attempts`) and
     Drills (`GET /admin/rollback2/drills`).
   - Good: latest `rollback_attempts` row has `result == "success"`
     (penalty 0). A `"restored_unverified"` row does **not** count as
     success (penalty 30).
   - The weekly drill is wired correctly in code
     (`backend/services/rollback_drill_cron.py`, 7-day interval, 900 s
     startup delay, registered in `backend/main.py` lifespan, supervised
     task `rollback_drill`); it returns `"blocked"` (no ledger row) when
     `AUREM_DRILL_REPO` or writable auth (GitHub App / `AUREM_DRILL_TOKEN`)
     is missing — so a missing drill record means **config**, not a code
     bug. A real drill must actually run; a record must actually exist.
     (A sibling PR #346 fixed the *backup* side of this ledger; the
     rollback side is untouched by any PR.)
6. **Expected score:** `pass_rate * 100 − penalty`. With pass_rate ≈ 0
   the score stays **0 even with penalty 0**. It moves only when the
   suite is green. **No code change can move it before Dec 1.**

## Honest limits

- Exact CI pass rate: unverifiable from the VM (no workflow-runs tool
  exists in the GitHub connector; only the Actions UI / Admin evidence
  show it).
- Rollback-penalty state: unverifiable from the VM (latest ledger row
  lives only in Atlas).
- Full-suite green: never run on this VM (no repo checkout, no git
  credentials, `gh` not logged in); only targeted file-level tests ran.
