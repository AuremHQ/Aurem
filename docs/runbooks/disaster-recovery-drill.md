> **Status:** ✅ VERIFIED — backup + restore pipeline code read end-to-end (Nov 21, 2026) · ⚠️ PARTIALLY VERIFIED — the production drill itself is still a Teji-step (see below)

# Disaster-Recovery Drill Runbook — Ora by Aurem

## What "Data Handling" actually measures

`backend/services/health_score.py::score_data_handling` reads three live facts from the app database — nothing else moves this score:

| Fact | Source collection | Score effect |
|---|---|---|
| Latest backup succeeded | `backup_history` (newest by `created_at`, `status == "success"`) | −30 if not `success` |
| Latest restore drill passed | `restore_drill_history` (newest by `checked_at`, `ok == true`) | −40 if `ok` is false; if ok, −(1 − `collection_coverage`) × 50 |
| Rollback evidence | `rollback_attempts` (v2 ledger) or legacy `rollback_status` | −0 on success, −25 if no evidence, −30 if the last attempt failed |

- **Unscored (not 0)** if there is no backup row, no drill row, or the latest drill is older than **14 days**.
- **Sept 7, 2026 snapshot: 100/100** (backup success, drill ok, coverage 1.0, rollback penalty 0). **Nov 20, 2026: 60/100** — a live-data regression, not a missing feature. The exact 40 missing points need the live Admin read below; the likely causes are a failed drill (−40) or a failed backup (−30) plus partial coverage.
- **The scorer, thresholds and weights are never edited to move this number.** Only real backup/drill/rollback facts move it.

## The pipeline (already built, enabled by default)

- **Nightly backup — 03:00 UTC** (`services/db_backup.py::backup_cron`, `ENABLE_DB_BACKUP=1` default). Python-native dump of every collection into a gzipped `aurem-native-v1` archive, uploaded to Cloudflare R2 (`mongo/` prefix), **30-day retention** (`BACKUP_RETENTION_DAYS=30`). Each run writes a `backup_history` row.
- **Weekly restore drill** (`services/restore_drill_cron.py`, `ENABLE_RESTORE_DRILL=1` default, every 7 days). Restores the newest restorable backup into a **scratch collection inside the same database** (never a second database — the Atlas user is scoped to one DB), diffs per-collection counts, writes a `restore_drill_history` row, and emails the founder on failure. Tries up to 5 recent `success` backups, newest first.
- **Weekly rollback drill** (`services/rollback_drill.py`, `ENABLE_ROLLBACK_DRILL=1` default). Snapshot → ship-bad → rollback → byte-exact verify; writes `rollback_drills` and feeds the `rollback_attempts` ledger the scorer reads.
- **Admin endpoints** (all under `/api/aurem-dev/admin/backups/*`, admin-gated): `GET /status` (backup history), `GET /list` (R2 objects), `POST /run` (backup now), `GET /drill-history`, `POST /drill-now` (drill now), `POST /test-restore` (restore any key to scratch).

## How to read the current state (Teji-step, 1 minute)

1. Open the Admin panel → **Backups** (or `GET /api/aurem-dev/admin/backups/status` + `GET /api/aurem-dev/admin/backups/drill-history`).
2. Read three lines: latest backup `status` + `created_at`; latest drill `ok` + `collection_coverage` + `checked_at` (+ `restore_error` if it failed); rollback penalty from the Codebase Health → Data Handling evidence card.
3. That pins down exactly which of the 40 points is missing — no guessing.

## How to run a drill now (Teji-step, ~1 minute of clicking)

1. Admin → **Backups** → **Run drill now** (`POST /admin/backups/drill-now`). It blocks until done.
2. Last known live timing: **~8 seconds** for 46k+ docs / 138 collections, coverage 1.0 (live verification, Aug/Sep 2026 — `test_reports/iteration_369.json`).
3. Expected result: a fresh `restore_drill_history` row with `ok: true`, `collection_coverage: 1.0`. If the latest backup is also `success` and the rollback ledger shows a success, **Data Handling reads 100** on the next score computation.
4. If the drill fails: read `restore_error` on the drill-history row first. Common causes: the newest `success` backup's R2 object is missing (the drill falls back through 5 candidates automatically); coverage below 0.85 (live DB gained collections since the last backup — run a fresh backup first with `POST /run`, then drill again).

## What this PR fixed (2026-11-21)

1. **Backup "success" is now verified against R2** (`services/db_backup.py::_verify_r2_object`). After upload, the code HEADs the object and checks its size. A "success" row whose object is actually missing — proven live Aug 23, 2026 — can no longer be recorded; it is marked `failed` and the next night retries. Success rows carry `r2_verified: true`.
2. **Failed mid-archive restores no longer orphan scratch data** (`services/db_restore.py`). The "bad collection header" and "unexpected EOF" early returns now drop the partial scratch collection (when `drop_scratch_after` is true), matching the exception path that already did.
3. **Tests:** 7 new tests, all passing locally — 3 pure unit tests for the R2 verifier, 4 mock-based restore tests (both failure paths proven to FAIL on the unfixed code and PASS on the fixed code). 1 env-gated integration test for the full backup path runs where real Mongo is available.
4. **Local end-to-end drill proof** (mock Mongo + local-file R2 stand-in, NOT production data): backup ok, `r2_verified: true`; drill ok, coverage 1.0, 6 docs / 4 collections, zero scratch leftovers; lost-object backup correctly recorded `failed`. Full log kept with the PR author, not in the repo.

## RPO / RTO (plain words)

- **RPO (how much data you can lose): up to 24 hours** — backups run nightly at 03:00 UTC.
- **RTO (how fast you get it back): minutes** — the restore itself is seconds-to-minutes for this database size; the drill proves it weekly.
- **Retention: 30 days** of nightly backups in R2.

## What is still NOT done here

- The **production drill run** itself — it needs the live database + R2, which this change cannot touch. Run it from the Admin panel (above) after this PR merges and deploys.
- The exact 40-point decomposition for Nov 20 — needs the 1-minute live Admin read above.
