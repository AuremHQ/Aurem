"""Admin Team S1 (AT1a) -> superseded by S2.

This file documented S1's TRANSITIONAL "no behaviour change, just
record what the new rules would refuse" gate. S2 (AT1b) turns on real
enforcement — a legacy tier=="founder" account not on the allow-list is
now actually REFUSED (403), not merely recorded while still getting
200. The `admin_gate_would_refuse_log` recording code itself was
removed from `require_admin` in S2 (nothing left to "simulate" once D1
is actually enforced).

The real S2 lock tests, including the direct replacement for this
file's "legacy founder" case (now asserting 403, not "200 + recorded"),
live in test_regression_iter_adminteam_s2_enforcement.py:
  - test_d1_legacy_is_admin_tier_founder_is_founder_grants_nothing
  - test_d1_owner_allowlist_email_full_access (replaces the
    owner-session-has-empty-would-refuse-record case below — the Owner
    simply gets 200 now, there is no would-refuse log left to be empty)

Kept as an empty module (not deleted outright) so STAGE-1.md's and
STAGE-2.md's file references still resolve to something explaining the
transition, per this loop's "no production bypasses, explain stage
transitions" spirit.
"""
