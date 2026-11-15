# Aurem Loop Harness — Release Checklist

**Status: DRAFT.** This is the checklist itself, for Teji to tick off —
not an execution script, and nothing on it has been done. No release,
no merge, no deploy, and the `harness_page` flag stays OFF until every
box here is actually checked, in order, by a human with the authority
to check it.

Scope: this document defines what "done" means for the FIRST real,
hosted release of the Aurem Loop harness (the fix/10of10 package).
Phase A (verify endpoint) and Phase B (page/download/releases/admin,
against synthetic `test_fixture` artifacts only) are already built and
tested — see the Phase A/B PR. Nothing on this checklist is satisfied
by that work alone; it all assumes the real package exists.

---

## 1. D1 approval

- [ ] `docs/SCOREBOARD.md` in the harness repo (branch `fix/10of10`,
      HEAD `deea337` at last check — re-confirm the HEAD hasn't moved)
      has been scored by Teji.
- [ ] An independent review of the `fix/10of10` package has been
      completed by someone other than its author (Muse).
- [ ] Teji has recorded explicit approval to host this specific
      package — a dated note naming the exact commit hash approved is
      enough; approving "the harness" in general is not.
- [ ] The approved commit hash is written down somewhere durable
      (this checklist, a release note, or the admin release record's
      own `created_by`/notes field) so a later audit can trace a
      hosted release back to the exact approval it came from.

## 2. Test gates

All of the following must be green, with real output pasted into the
release record (not just "ran fine"):

- [ ] Phase A backend suite: 31/31 passing
      (`tests/test_regression_iter2026_63_harness_verify_phase_a.py`).
- [ ] Phase B backend suite: 29/29 passing
      (`tests/test_regression_iter2026_64_harness_phase_b_releases_and_downloads.py`).
- [ ] Phase B frontend suite: 10/10 passing
      (`src/pages/__tests__/HarnessPage.test.jsx`) — re-run and update
      this count if the page design work's test file replaced it with
      a different count; reconcile before checking this box.
- [ ] Full backend regression green, except the one already-confirmed
      pre-existing, unrelated failure
      (`test_regression_iter_adminteam_s1_fitness.py`'s admin-route
      fitness check, 21 missing registry entries, none of them
      `/harness`) — if that failure's shape has changed, stop and
      investigate before proceeding; don't assume it's still the same
      pre-existing issue.
- [ ] Full frontend regression green (225 files / 1168 tests at last
      count — re-run, don't assume the count is still current).
- [ ] `ruff check backend/` from repo root: zero NEW findings vs. the
      pre-Phase-A baseline (set-diff method, not a raw count).
- [ ] Fresh-PC install test, **free account**: install completes,
      `loop.py auth` reports free/unverified correctly, a local task
      runs.
- [ ] Fresh-PC install test, **subscriber account**: install
      completes, `loop.py auth` reports subscriber, cloud option
      (once built) is reachable.
- [ ] `loop.py auth` + revoke-refuses test, run against the REAL
      package on a REAL Windows machine (not the server-side
      equivalent already proven in the Phase A/B PR — that proved the
      server half; this is the client half, not yet done anywhere).
- [ ] Update-check (`GET /api/harness/latest`) test — only applicable
      once that endpoint exists (Phase B3 / plan §7); if B3 hasn't
      shipped yet, check this box N/A and say so explicitly rather
      than silently skipping it.

## 3. Docs

- [ ] Release notes / changelog entry written for this version,
      reviewed for honesty against the same constraints as the
      `/harness` page copy (no Ollama claim, no benchmark number
      claim, no "Loop C edited weights" claim unless that has
      genuinely changed).
- [ ] `/harness` page copy (`frontend/src/data/harnessPage.mjs`)
      reviewed against the actual released package — if anything
      about what it does/doesn't do changed between the test-fixture
      build this page was designed against and the real `deea337`
      package, the copy is updated to match, not left stale.
- [ ] `llms.txt` section for Aurem Loop added or updated, consistent
      with the same honesty constraints.
- [ ] `README.md` updated if it claims anything about harness
      availability that would become stale or contradictory once this
      ships.

## 4. Published-release artifact

- [ ] Exact file list matches the approved package: `loop.py` (33-line
      launcher), `aurem_loop/` (45 modules), `version.json`,
      fix-branch `install.bat` / `install.ps1`, `skills/` (6 packs),
      generated `docs/` — confirm this list against the actual zipped
      artifact byte-for-byte, not from memory.
- [ ] `version.json` version string matches the release record's
      `version` field exactly.
- [ ] A release manifest (`artifact_sha256` + `signature`, via
      `services/harness_releases.py::create_draft_release`) has been
      generated from the REAL artifact bytes — not reused from any
      earlier test-fixture release.
- [ ] The release's `source` field is set to
      `"muse_fix_branch_approved"` — never `"test_fixture"` for a real
      release, and never left as whatever the admin form defaulted to
      without checking.
- [ ] Changelog entries attached to the release record, same content
      as the public release notes from §3.
- [ ] Published in admin via `POST /admin/harness/releases` (draft)
      then `POST /admin/harness/releases/{release_id}/publish` — two
      separate, explicit actions, not one combined step.
- [ ] Confirm `GET /admin/harness/releases` shows exactly one
      `is_latest: true` row for platform `windows`, and that it's the
      one just published (not an old test-fixture row left over).
- [ ] Confirm `POST /harness/download` (as a real logged-in test
      account) actually serves THIS artifact — check the downloaded
      zip's `identity.json` is correctly injected and the rest of the
      archive matches the approved file list from the first box above.

## 5. Go-live sequence

Order matters — each step confirmed working before the next starts.

- [ ] 1. Publish the release (§4, already done by this point — this
      step is the confirmation, not the action).
- [ ] 2. Verify download: a real test account (free and subscriber,
      separately) downloads successfully and the zip contents check
      out.
- [ ] 3. Turn the `harness_page` flag ON. Per the current build
      wiring (`scripts/seo-prerender.mjs`), the homepage promo section
      and the `/harness` page's own static snapshot are both gated by
      the exact same flag read — they go live together, by
      construction, not as two separate steps that could drift out of
      sync.
- [ ] 4. Live curl checks against the real production domain (not
      localhost):
      `curl https://auremcto.com/api/harness/page-status` returns
      `{"harness_page_enabled": true}`;
      `curl -I https://auremcto.com/harness` returns a real page (not
      the generic SPA shell fallback);
      `curl -X POST https://auremcto.com/api/harness/verify` with a
      real test bin/token returns the expected shape.
- [ ] 5. Redeploy via the actual platform mechanism
      (`docs/DEPLOYMENT.md`: Emergent Dashboard → Manage Deployments →
      Re-deploy) — not a raw `git push`, which this environment has no
      credentials for anyway.
- [ ] 6. Spot-check the live `/harness` page in a real browser, signed
      out and signed in, on both desktop and mobile.

## 6. Rollback

- [ ] Documented, tested trigger conditions for pulling the release
      (e.g., a reported installer failure, a signature/checksum
      mismatch report, a security finding) — written down before
      launch, not improvised during an incident.
- [ ] Pull the release: set the published release's `is_latest` back
      to `false` (no release = `POST /harness/download` returns a
      clean 409, same as "no release published yet" — never serves a
      broken artifact instead of refusing).
- [ ] Turn the `harness_page` flag back OFF — homepage promo and
      `/harness` page both disappear together, same single-flag
      guarantee as go-live step 3.
- [ ] Confirm existing installations are NOT affected by a rollback:
      already-downloaded harnesses keep running local tasks regardless
      (per the hybrid design — revocation/rollback never touches free
      local functionality or deletes local user data); only new
      downloads and new `/harness` page visits are affected.
- [ ] Redeploy via the same platform mechanism as go-live step 5 to
      make the flag-off / release-pulled state live.
- [ ] Post-rollback note recorded: what triggered it, what was rolled
      back, and what needs to happen before attempting go-live again.
