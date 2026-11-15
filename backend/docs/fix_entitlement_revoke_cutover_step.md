# Fix-entitlement stale-row cutover step (ITEM 1(d))

Scope note: this is the narrow cutover step for ITEM 1's specific bug
(stale `fix_entitlement=True` on cancelled/paused users from before
the webhook-revoke fix). The full, repo-wide token-billing cutover
checklist is a separate deliverable (ITEM 5(c),
`docs/CUTOVER_TOKEN_BILLING.md`) and will reference this step rather
than duplicate it.

## What is already built (read-only, safe to run anytime)

1. `python3 scripts/fix_entitlement_preflight.py --out <path>` (or
   `GET /admin-pricing/stale-fix-entitlement-preflight`, admin-only) —
   scans every `dev_users` row with `fix_entitlement: True` and lists
   the ones with no current active-entitlement signal (cancelled/
   paused subscription, no purchased/plan balance). Writes nothing —
   `services.fix_entitlement.find_stale_entitlements` refuses a raw
   (unguarded) db handle.
2. `services.fix_entitlement.is_fix_entitled` already self-heals this
   exact shape at request time (returns `False` for a stale row even
   without any backfill), so the bug is contained today without the
   step below. The pre-flight report exists so the founder can SEE
   the blast radius before deciding whether to also clean up the
   stored rows.

## What is NOT built yet (future, requires founder sign-off)

A REAL revoke pass — writing `fix_entitlement: False` to every row the
pre-flight lists — is deliberately not built in this fix round. Before
it exists, it must have:

- An explicit admin-confirm-token flow (same pattern as
  `routers/admin_pricing.py`'s pricing-mutation confirm-token gate —
  `_ADMIN_MUTATE_RATE_PER_MIN`/confirm-attempt collection — not a bare
  POST with no second factor).
- A dry-run-first requirement: the endpoint must refuse to run unless
  it can show the caller the same pre-flight list first, so there is
  no path to a surprise mass-write.
- An audit trail per revoked row (`fix_entitlement_revoked_reason`,
  `_at`, and the admin's id — `revoke_fix_entitlement` already writes
  reason/timestamp; the real-revoke tool should pass the admin's user
  id into `reason` too).
- Founder review of the actual pre-flight output first — the list may
  be empty (most likely, since this is a brand-new rollout) or may
  contain rows the founder wants to grandfather rather than revoke.

## Cutover order

1. Run the pre-flight (read-only) and review the CSV with the founder.
2. Founder decides, per row or in bulk, who actually gets revoked.
3. (Future) Build and run the admin-confirm-token real-revoke tool
   against exactly the reviewed list — never "everyone the scan
   found" unattended.
4. Re-run the pre-flight to confirm the list is now empty.
