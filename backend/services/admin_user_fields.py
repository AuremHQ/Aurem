"""FIX 3 (2026-02-03) — allowlist of `dev_users` fields safe to return in an
admin-facing response.

Replaces the old exclusion-style projection (`{"password": 0, ...}`) used by
`GET /admin/users` and `GET /admin/users/{user_id}`. An exclusion list only
blocks fields it knows about today; any field added to `dev_users` later
(by any future feature) would leak by default. This allowlist inverts that:
a new field is invisible to every admin response until someone deliberately
adds it here.

Built from the full field set observed on Preview's `dev_users` collection
plus every field written by `routers/mfa.py`, `routers/admin_users.py`
(suspend/grant/loop-beta), and `routers/admin_bin.py` (tier change), minus
the fields that must never reach an admin response:
  password, password_hash, mfa_secret, mfa_secret_pending,
  mfa_backup_codes, mfa_backup_codes_pending, ora_pin_hash, and the whole
  `github` sub-document (today it holds only `access_token` — nothing
  inside it is currently safe to expose, so the sub-document is dropped
  entirely rather than attempting a nested exclusion, which MongoDB cannot
  mix with a top-level inclusion projection in one query).
"""

DEV_USER_SAFE_FIELDS: list[str] = [
    "user_id",
    "email",
    "email_verified",
    "name",
    "phone",
    "tier",
    "tier_updated_at",
    "tier_updated_by",
    "is_admin",
    "is_founder",
    "is_unlimited",
    "created_at",
    "updated_at",
    "last_seen_at",
    "last_real_task_message_at",
    "first_chat_at",
    "first_chat_opened_at",
    "first_loop_at",
    "first_scan_at",
    "first_ship_at",
    "first_task_submitted_at",
    "tokens_granted",
    "tokens_remaining",
    "track",
    "track_updated_at",
    "trust_level",
    "pending_nudge",
    "personal_nudge_clicked_at",
    "post_skip_nudge_count",
    "repo_connect_gate_skipped",
    "promo_first50_claimed",
    "quota_used",
    "payment_failed",
    "stripe_sub_id",
    "failed_logins",
    "last_failed_at",
    "signup_ip",
    "session_barrier_at",
    "session_barrier_reason",
    "mfa_enabled",
    "suspended",
    "suspended_at",
    "loop_beta_enabled",
    "loop_beta_updated_at",
    "loop_beta_updated_by",
]

# Inclusion-style Mongo projection built from the allowlist above. `_id` is
# excluded explicitly (the one field Mongo allows to differ in kind from
# the rest of an inclusion projection).
DEV_USER_SAFE_PROJECTION: dict = {"_id": 0, **{f: 1 for f in DEV_USER_SAFE_FIELDS}}

# Fields that must never appear in an admin response, for the regression
# test to assert against directly (kept in sync with the module docstring).
DEV_USER_FORBIDDEN_FIELDS: list[str] = [
    "password",
    "password_hash",
    "mfa_secret",
    "mfa_secret_pending",
    "mfa_backup_codes",
    "mfa_backup_codes_pending",
    "ora_pin_hash",
    "github",
]
