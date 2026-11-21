# Test Coverage scan — runbook

How the Codebase Health Score's **Test Coverage** category (weight 10)
gets scored, why it is unscored right now, and the exact steps to score
it. Read from the code on main `3332b60e` (Nov 21, 2026), not guessed.

## 1. What the scorer needs

`backend/services/health_score.py::score_test_coverage` reads the latest
doc in the Atlas collection **`health_test_coverage_runs`**
(sorted by `generated_at`, newest first).

- **No doc at all** → UNSCORED: "No coverage run yet — trigger one from
  the widget (admin: POST /admin/health-score/test-coverage/run)."
- **`ok` false, or `overall_coverage_pct` missing** → UNSCORED: "Last run
  did not complete cleanly: {error}".
- **Doc older than 7 days** (`_TEST_COVERAGE_STALE_DAYS = 7`) → UNSCORED:
  "Stale — last real coverage run was {age}d ago (threshold 7d). Run again."
- **Otherwise, score = 0.5 × `overall_coverage_pct` + 0.5 ×
  `critical_hit_frac` × 100.**

The doc the run writes (`backend/services/health_coverage_scan.py`):
`generated_at`, `ok`, `scope`, `commit_sha`, `overall_coverage_pct`,
`critical_modules` (5, see §3), `critical_hit_frac`, `test_counts`,
`duration_s`, `source: "live_run_in_pod"`.

Weight: 10 of 100. While unscored the category is excluded from the
weighted mean (denominator 90, not 100). Once scored it enters the mean,
so a coverage score of S moves the overall to ≈ 63.4 + S/10 against the
Nov 20 baseline (overall ~70 with this category excluded) — e.g. S=80
→ ~71.4. If PRs #344/#346/#347 are merged first, the baseline is higher
and the same formula applies on top of it. The scorer recomputes live;
this is arithmetic, not a promise.

## 2. What the run actually does

`backend/services/health_coverage_scan.py::run_coverage_scan(db)`,
triggered by `POST /admin/health-score/test-coverage/run`
(`backend/routers/admin_health_score.py`, admin-only):

```
python -m pytest -q --timeout=30 \
  -k "auth or chat or findings or fix_pipeline or payment" \
  --cov=. --cov-report=json --continue-on-collection-errors \
  --ignore=tests/test_iter138_acceptance_seven.py \
  --ignore=tests/test_iter212m163_aggression_chat.py
```

- Scoped deliberately: the full ~5,300-test suite with `--cov` did not
  finish in 600 s (measured Aug 23, 2026). The `scope` field in the
  persisted doc always discloses this is NOT whole-repo coverage.
- Subprocess timeout 900 s (`HEALTH_COVERAGE_TIMEOUT_S`, default 900;
  nothing in the repo sets it, so the preview pod uses 900).
- Last known good run: **Sept 7, 2026 — 855 passed, 32 failed, 3 errors
  in 428.11 s** (counts locked in `backend/tests/unit/coverage_scan_test.py`).
  The coverage % from that run was never recorded anywhere in the repo,
  so **no coverage number can be quoted until a fresh run lands** — this
  PR quotes none.
- A run with failing tests still persists a score (classification only
  distrusts a 0-test run, which means the output could not be trusted).
  A timed-out run, a 0-test run, or a subprocess crash persists
  **nothing** — the scorer keeps showing the previous state.
- Concurrency: an atomic Mongo lock (`health_coverage_scan_lock`) makes
  a second overlapping trigger a fast no-op; the endpoint answers 409
  "A coverage scan is already running" instead.

Critical modules (coverage > 0% in any one counts as a "hit"):

- `cto_services/auth.py`
- `routers/chat/stream.py`
- `routers/findings.py`
- `routers/fix_pipeline.py`
- `routers/admin_payments.py`

All five paths verified present on main `3332b60e`, as are both
`--ignore` files.

## 3. Why production returns 409

Deliberate guard (2026-09-20, deploy-health fix round 5). The
pytest+coverage subprocess is genuinely CPU-heavy and shares the pod's
CPU cgroup quota with the FastAPI process serving `/health`; on
production it flapped live health checks for real users. So
`POST /admin/health-score/test-coverage/run` refuses outright when
`_env_from_host("") == "production"` (409: "Test-coverage scan is
disabled in production … Run this from a preview environment instead").
There is no separate worker pod to move it to. **Do not remove this
guard — run from preview instead.**

## 4. The preview run — exact Teji-steps

The preview deployment shares the Atlas database with production (same
users, same projects — verified live Nov 20), so a run triggered on
preview is exactly what the production scorer reads. Preview verified
alive Nov 21: `https://bin-context-pat.preview.emergentagent.com/api/health`
→ HTTP 200, `env: "preview"`, build `3332b60e970e`, `db: true`. If that
preview link has rotated, use the current app preview link from
Emergent — any non-production deployment works; `auremcto.com` will
409 by design.

1. Open the preview link above and sign in as admin
   (`teji.ss1986@gmail.com`, founder tier).
2. Open the Admin panel → **Codebase Health Score** widget.
3. Click **Run coverage** (`data-testid="health-score-run-coverage-btn"`).
4. Wait ~7–8 minutes. The widget polls every 15 s (this PR extends the
   poll window from 24 to 40 polls = 600 s, because the measured run
   takes ~430 s and the old 360 s window stopped polling before the run
   finished). The run continues server-side even if you close the tab.
5. Verify: reload the widget (or `GET /admin/health-score`) — Test
   Coverage should show a score with a fresh `last_verified` (today's
   date). That is the proof; nothing else needs checking.

## 5. If it does not score

- **409 "A coverage scan is already running"** → wait a few minutes and
  poll again; do not retrigger.
- **409 "disabled in production"** → you are on `auremcto.com`, not the
  preview. Use the preview link in §4.
- **Widget still shows stale data after ~10 minutes** → the run failed
  or timed out (over 900 s). Failed runs persist no doc, so the widget
  cannot show the reason — check the backend log for
  `health_coverage_scan` (timeout / 0-tests / collection error), then
  retry once. If it times out again, the scoped suite has outgrown the
  900 s cap: raise `HEALTH_COVERAGE_TIMEOUT_S` on the preview pod (env
  var, no code change) and retry.
- **Score lower than expected** → read the doc's `critical_modules`:
  any critical module at 0% costs 10 points (0.5 × 20 per module).
  That means no test in the scoped subset executed that module — a real
  coverage gap, not a scorer bug.

## 6. What this PR changed (and did not)

Changed (branch `health-test-coverage-2026-11-21`):

1. `frontend/src/components/HealthScoreWidget.jsx` — poll loop 24 → 40
   polls (360 s → 600 s) with a comment citing the measured 430 s run.
   The loop still breaks early when a fresh `last_verified` appears.
2. This runbook (`docs/runbooks/test-coverage-scan.md`).

NOT changed: the scorer, thresholds, weights, the 900 s subprocess
timeout, the prod 409 guard, the scope filter, the critical-module
list, and no test weakened, skipped, or deleted. No coverage number
was invented or written anywhere — the first real number comes from the
preview run in §4.

Frontend tests for the widget could not be run from this agent (no
workspace/browser access); the change is a loop-bound constant plus a
comment, and CI runs the widget suite on the PR. Stated plainly so
nobody has to take it on trust.
