# Admin Team — Overnight Loop: MORNING REPORT

All four stages completed. **S0 -> S1 -> S2 -> S3, all GATE MET.**

## Summary

| Stage | What | Commit(s) | Tests |
|---|---|---|---|
| S0 | FIX 3 — allowlist `dev_users` fields in GET /admin/users(+/{id}) | `6dbec14d` | 2 |
| S1 | AT1a — route registry (336 entries) + one shared request-aware gate, legacy/no-behaviour-change mode | `0c552a95` | 2 |
| S2 | AT1b — real enforcement: founder allow-list only, `admin_team_members`, MFA session claim, developer role, D12 fail-closed | `8aa59e3d` | 16 |
| S3 | AT2 — Team screen + invite flow (hashed one-time tokens, no email sending, owner notification bell) | checkpoint + `3832ed9e` | 7 |

27 new backend regression tests, all passing. One full-stack testing
pass (first for this feature) found and fixed 2 real bugs (both in
S3's frontend wiring, not the security logic) — see
`docs/admin-team/STAGE-3.md`.

## Key things now true that weren't true last night

- **Owner is only a `FOUNDER_EMAILS` email.** `is_admin=true`,
  `tier=="founder"`, `is_founder=true` in `dev_users` grant NOTHING by
  themselves any more, anywhere in the codebase (D1/D14/D18). Verified
  with a lock test that mints a token for an account carrying all
  three legacy flags, not on the allow-list — refused (403).
- **One real gate.** `cto_services.auth._finish_admin_check` is the
  only place the actual decision is made. The 5 other places that
  used to re-implement `is_admin or tier=="founder"` independently
  (`routers/_admin_common.py`, `admin.py`, `mfa.py`,
  `admin_vanguard.py`, `admin_bin.py`) all delegate to it now.
- **Team members exist as a real, separate concept** from "admin" —
  `admin_team_members`, keyed by `user_id`, never implies
  `dev_users.is_admin=true` (D21, lock-tested). Their access is capped
  per-screen by `services/admin_access_registry.py`'s 336-route map,
  and requires a `mfa` JWT claim earned by actually completing 2FA
  (D6) — except mfa.py's 3 self-service bootstrap routes.
- **D12 fail-closed**: no confirmed current `dev_users` row means 403;
  a DB exception during that check means 503. The bare JWT `is_admin`
  claim is never trusted on its own again.
- **The Owner can now invite a developer**, hand them a one-time
  link (only its SHA-256 hash is ever stored), and manage/suspend/
  resume/remove them from a real screen at `/admin/team` — with every
  action audited (`admin_audit`) and the Owner notified when someone
  accepts.

## What's deliberately NOT done (out of scope for this loop)

- **AT3** — full widget-level 403 hiding across every existing admin
  page for a team member's trimmed view (D10 explicitly defers this;
  today a team member who accepts an invite has correct backend
  access per their overrides, but the frontend route guard
  (`isAdminOrFounder`) and sidebar are unchanged — they can reach
  `/admin/team`'s accept flow and any admin API they're entitled to
  directly/via a deep link, but the sidebar still reflects the old
  Owner-only nav set). Flagging this explicitly so it isn't mistaken
  for a bug: it's a known, intentional boundary of this loop, and the
  natural next increment.
- **AT4** — removing the `ADMIN_EMAIL`/`ADMIN_EMAILS` promotion code
  path in `routers/auth.py` (D2 said it stops granting access via S2,
  which it does — `tier`/`is_admin` alone grant nothing now — but the
  promotion code itself, and the env vars, still exist unused).
- Real 2FA-claim wiring for the **PIN-login flow**
  (`routers/ora_chat.py`'s PIN endpoints) — explicitly out of scope
  per D6; those tokens simply don't carry the `mfa` claim, which is
  fine because only the Owner can use a PIN-minted token under the
  new gate anyway.
- The canary (`services/ora_chat/canary.py`) and the existing
  workspace Team feature (`routers/team_management.py`,
  `AcceptInvite.jsx`, `db.team_members`) were not touched, as required.

## Process note (not a stop condition, but worth flagging)

Mid-S1, this environment had an unrelated infrastructure interruption
(a background process and a temp log were lost, likely coincident
with an external deploy notification unrelated to this work, not
something this loop initiated). No code or test file was lost; only
an in-progress background pytest run had to be restarted. Documented
here per the loop's "feedback loops matter" spirit, not because it
met any of the stop conditions; the work itself rebased cleanly and
continued without needing a decision or losing any committed progress.

## Try it on Preview

1. Log in as the Owner: `test@aurem.dev` / `AuremTest2026!`
   (see `/app/memory/test_credentials.md`).
2. Go to Admin -> Admin Team in the left nav (`/admin/team`).
3. Click "+ Invite developer", enter any email, copy the link shown
   (shown once).
4. In a different browser/incognito, log in as a different account
   and open the copied link (`/team-invite/accept?token=...`) and
   click Accept invite -> "You're in!".
5. Back on the Owner's Team screen, the new row appears under
   Members; try Suspend/Resume/Overrides/Remove.
