# Admin Team — STAGE 0 (FIX 3: allowlist admin-user response fields)

Status: **GATE MET**

Commit: `6dbec14d` — "Admin Team S0: allowlist dev_users fields in GET
/admin/users and GET /admin/users/{id} (FIX 3)"

## What changed

- `backend/services/admin_user_fields.py` (new) — an explicit allowlist
  of `dev_users` fields safe to return from admin endpoints, instead of
  the previous exclusion-style projection (which silently exposes any
  NEW sensitive field added to the collection later).
- `backend/routers/admin_users.py` — `GET /admin/users` (list) and
  `GET /admin/users/{id}` (detail) now build their response through
  this allowlist. The detail endpoint also gets a defense-in-depth
  final sanitization pass before returning, since its enrichment logic
  assembles the response from several sources.
- Never present in either response, by construction: `password`,
  `password_hash`, `mfa_secret`, `mfa_secret_pending`,
  `mfa_backup_codes`, `mfa_backup_codes_pending`, `ora_pin_hash`,
  `github.access_token`, or any unknown/future field not on the
  allowlist.

## Evidence

- New behavioural tests
  (`tests/test_regression_iter_adminteam_s0_allowlist.py`): seed a
  `dev_users` row with every prohibited field PLUS an unknown field,
  call both endpoints, assert none of those fields appear anywhere in
  the response. Both pass.
- Preview curl as Owner (`test@aurem.dev`) against the live backend —
  `GET /admin/users?limit=1` field-name list for the first user
  contains no secret-shaped key (no `password*`, `mfa*`, `pin*`,
  `token*` field names).
- No other admin endpoint was found reading `dev_users` with a broader
  projection than it needs (checked during the Turn-A mapping phase).

## Next stage

S1 (AT1a) — route registry + one shared request-aware gate, in
legacy/no-behaviour-change mode.
