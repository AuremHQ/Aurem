# Admin Team — STAGE 3 / AT2 (Team screen + invite flow)

Status: **GATE MET**

Commits: bulk code in the "checkpoint before testing_agent_full_stack"
commit (auto-created by the testing pass), fixes in `3832ed9e`.

## What changed

- `backend/routers/admin_team.py` (new) — owner-only management:
  `GET /admin/team/members`, `GET /admin/team/invites`,
  `GET /admin/team/screens`, `POST /admin/team/invites`,
  `POST /admin/team/invites/{id}/copy-link` (D8 — rotates the token,
  the old raw token stops matching), `POST /admin/team/invites/{id}/cancel`,
  `POST /admin/team/members/{user_id}/suspend|resume|remove`,
  `PATCH /admin/team/members/{user_id}/overrides`. All 10 routes
  registered as `OWNER_ONLY` in the registry (Team management is
  itself owner-only — no team member manages the team, including
  their own role).
- `backend/routers/team_invite_accept.py` (new) — `POST
  /team-invite/accept`, deliberately NOT admin-gated (the accepting
  person isn't an admin yet): matches the SHA-256 hash of the posted
  token against a pending invite, checks the accepting user's email
  matches the invite's email, creates/reactivates their
  `admin_team_members` row (status=active, `dev_users.is_admin` stays
  false per D21), marks the invite accepted, writes an `admin_audit`
  row, and sends the Owner a bell notification via
  `services/notifications.py::emit_notification`. Returns `{ok, role}`
  only — no token/session, per spec.
- `frontend/src/pages/AdminTeamPage.jsx` (new) — Owner's Team screen:
  Members table (suspend/resume/remove/overrides dialog) + Pending
  Invites table (copy-link/cancel) + a "+ Invite developer" dialog
  that shows the raw invite link exactly once.
- `frontend/src/pages/TeamInviteAccept.jsx` (new) — `/team-invite/accept?token=...`,
  inside the normal `PrivateRoute` (not `AdminRoute` — the person
  accepting isn't an admin yet). Falls back to `GET /auth/me` if the
  cached user object is stale.
- Wired into `adminNav.js` ("Admin Team" item) and `App.jsx`
  (`/admin/team`, `/team-invite/accept` routes). The EXISTING
  workspace Team feature (`/team`, `/team/accept-invite`,
  `db.team_members`, `AcceptInvite.jsx`) was not touched.

## Evidence

- `tests/test_regression_iter_adminteam_s3_invites.py` (new, 7 tests,
  `@pytest.mark.real_admin_gate`, real Preview Mongo + real server.app):
  create/list/cancel an invite with the token never persisted raw;
  non-owner/non-member refused on every `/admin/team/*` route; the full
  create -> accept flow creates an active member, flips nothing on
  `dev_users.is_admin`, marks the invite accepted, and fires the owner
  notification — then proves the SAME raw token can't be replayed;
  accepting with the wrong email is refused; `copy-link` rotates the
  token so the old one stops working; suspend/resume cycle; overrides
  validate the level string and persist. All 7 pass, plus the existing
  20 S0-S2 tests still pass (27/27 total).
- Full-stack testing pass (first one for this entire feature,
  `test_reports/iteration_adminteam_s0_s3_full_verify_2026_10_05.json`):
  25/25 backend checks and 6/6 frontend UI flows passed after 2 real
  bugs were found and fixed — `Admin.jsx`'s icon map was missing the
  new nav item's icon (crashed the whole Admin panel on load) and the
  SPA fallback route allowlist (`vite-plugins/aurem-prod-fixes.mjs` +
  `Dockerfile`'s nginx config) didn't know about `/team-invite/*`,
  404-ing that route in prod-fix mode. Both fixed and re-verified.
- Preview curl as Owner post-fix: `GET /admin/team/members` -> 200.
- `/api/health` build hash after restart: `0f7c86a` (same documented
  one-commit lag as S1/S2).

## Loop complete

S0 -> S1 -> S2 -> S3 all GATE MET. See `MORNING-REPORT.md` for the
full summary and what's intentionally deferred beyond this loop (AT3's
full widget-level 403 filtering, AT4's `ADMIN_EMAIL` promotion
removal, the real pin-login 2FA-claim wiring — all explicitly out of
scope per the user's D6/D10 notes).
