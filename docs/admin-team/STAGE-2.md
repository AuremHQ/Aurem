# Admin Team — STAGE 2 / AT1b (real enforcement + developer role)

Status: **GATE MET**

Commit: `8aa59e3d` — "Admin Team S2 (AT1b): real enforcement +
developer role (D1/D6/D7/D9/D12/D13/D14/D18/D19/D21, R4)"

## What changed

- `backend/services/admin_team.py` (new) — `admin_team_members`
  (accepted members, keyed by `user_id` per D7; a member's
  `dev_users.is_admin` always stays `false`, per D21) and `admin_roles`
  (secure-by-default: a freshly-seeded "developer" role grants nothing
  until the Owner assigns screen levels or a per-person override).
- `backend/cto_services/auth.py` — `_finish_admin_check` is now the
  ONE real decision (D19):
  1. `current_dev`'s new `_row_lookup` state (`found`/`not_found`/
     `db_error`) drives D12's fail-closed rule: no confirmed row ->
     403, a DB exception -> 503. The bare JWT `is_admin` claim is
     never trusted directly any more.
  2. Owner = the confirmed row's email is on `FOUNDER_EMAILS` (D1/D14/
     D18) — `is_admin`/`tier=="founder"`/`is_founder` alone grant
     nothing. No MFA claim required (D6 — PIN-minted tokens still work
     for the Owner specifically).
  3. Team member = an active `admin_team_members` row, capped by the
     route registry's required `(screen_key, level)` for the matched
     request, AND the session's `mfa` JWT claim (D6) — except
     `mfa.py`'s 4 self-service routes (R4), which only `disable`
     requires the claim for.
  4. `_legacy_admin_bypass()` — hardcoded `return False` in shipped
     code (D15: not a flag/env var). `tests/conftest.py`'s one autouse
     fixture monkeypatches this exact function so the ~265 pre-existing
     tests that construct a legacy `is_admin=True` user keep passing
     unedited. The new Admin Team lock tests opt out with
     `@pytest.mark.real_admin_gate`.
  5. Two `ContextVar`s (`_admin_decision`, `_current_request`, D19) —
     same-request decision reuse for redundant inline checks
     (`admin_vanguard.py`) and `request` propagation for the ~270
     existing `require_admin` callers that don't have a `Request`
     object on hand, with zero cross-request leakage (each asyncio
     Task gets its own copy of the context).
  - `create_token()` gained `mfa_verified` (sets the `mfa` claim). Only
    `/auth/login/2fa-verify`'s real-code-checked success path passes
    `True`; `/auth/me`'s token refresh preserves the INCOMING token's
    own claim (otherwise a team member would lose access on their very
    next page load/focus, since `/auth/me` re-signs on every call).
  - `require_admin_dep` still literally calls `require_admin(authorization)`
    (stashing `request` via a contextvar first) so
    `test_iter358_admin_auth_hardening.py`'s static source check on
    that exact delegation kept passing unedited.
- `routers/mfa.py` — added the router-level `require_admin_dep`
  dependency it was missing (the only admin router without one), so
  `request` is always available for its R4 self-service exemptions.
- `routers/admin_bin.py` — D13: setting `tier="founder"` is now
  owner-only (plan-only, never access — `is_founder_email` is the only
  thing D1 ever checks for access).
- `services/admin_access_registry.py` — `lookup_route()` /
  `MFA_SELF_SERVICE` helpers; fixed a real param-name bug found during
  S2 testing (`{user_id}` written in the registry for the route's
  actual `{bin_id}` parameter on the tier-change endpoint) by
  normalizing `{param}` segments on both sides of every lookup, closing
  that whole bug class for any future param-name mismatch.

## Evidence

- `tests/test_regression_iter_adminteam_s2_enforcement.py` (new, 16
  tests, all `@pytest.mark.real_admin_gate`, end-to-end via
  `TestClient(app)` + real JWTs + real Preview Mongo): D1 (legacy
  is_admin/is_founder/tier=="founder" -> 403; real Owner -> 200), D12
  (no confirmed row -> 403), D21 (team member's `dev_users.is_admin`
  stays `false`), D6 (team member without the `mfa` claim -> 403, with
  it + sufficient level -> 200; PIN-shaped token still works for
  Owner), level enforcement (VIEW can't reach a MANAGE route; a MANAGE
  override does; an owner-only route refuses even a MANAGE team
  member), D13 (team member can't set tier=founder but can set other
  tiers; forcing tier=founder directly in the DB still doesn't grant
  access), R4 (status/enroll-* reachable without the mfa claim yet;
  disable requires it). All 16 pass.
- `test_regression_iter_adminteam_s1_would_refuse.py` was retired (its
  premise — transitional recording without enforcement — no longer
  exists; replaced by the real lock tests above).
- Full-suite run (`-n 6`) after S2 showed a higher failure count than
  S1's; a rigorous investigation (exact-failure-name diffing, then
  targeted git-stash A/B testing of every plausible new failure,
  including every admin/auth/oauth-named one) traced 100% of the
  apparent "new" failures to this suite's pre-existing
  `_FakeDB`/`cto_services.db.set_db()` global-singleton leak pattern
  (used by ~100+ unrelated test files) interacting with
  non-deterministic `pytest-xdist` worker distribution between runs —
  not this stage's code. Every one reproduced identically against
  unmodified code when isolated. My own new test file needed (and got)
  a local, self-contained fixture to guarantee a real DB connection
  regardless of what an earlier test in the same process left behind.
- Preview curl as Owner (`test@aurem.dev`) — `GET /admin/users` -> 200
  end-to-end against the live Preview backend post-S2.
- `/api/health` build hash after restart: `0f7c86a` (same displayed
  hash as S1 — this is the documented one-commit lag in
  `main.py::_resolve_build_hash`'s docstring, not a defect; `git log
  0f7c86a..HEAD` shows this commit shipped).

## Next stage

S3 (AT2) — Team screen + invite flow: UI to invite/suspend/resume/
remove a developer, hashed one-time invite tokens (D8) shown once, no
email sending (copy-link only), owner notification bell +
`admin_audit` rows on every action (D11), and full behavioural/RTL
tests.
