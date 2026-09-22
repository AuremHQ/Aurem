# Repo Write-Access Separation — Investigation (v2#3)

**Status:** Investigation only, per approved batch scope ("confirm/build separate repo write access" — investigation now, implementation is a separate later task if this confirms bundling). No auth/OAuth code was changed for this investigation.

_Last updated: 2026-09-22_

## Question
Does the GitHub App installation AUREM uses bundle read and write repo access together, with no way for a user to grant "read my repo" without also granting "write to my repo"?

## Finding: Yes, bundled — confirmed at the GitHub App permission-model level, not just in AUREM's own code

**Evidence (code, not GitHub docs alone):** the GitHub App manifest/permissions AUREM requests are captured directly in test fixtures that mirror the real installation payload:

```python
"permissions": {"contents": "write", "metadata": "read"}   # tests/test_github_app_router.py:322
"permissions": {"contents": "write"}                        # tests/test_github_app_router.py:479
```

`contents` is a single GitHub App permission with exactly three possible values: *No access*, *Read-only*, or *Read and write*. There is no fourth "write without read" option, and critically for this question, **there is also no way to request "read-only" for one operation and "write" for another** — it's one dial per installation. AUREM's GitHub App is configured at `contents: write`, which — by GitHub's own permission model, not an AUREM implementation choice — silently and necessarily includes read.

**Conclusion:** the bundling is a property of GitHub's own App permission system, not something AUREM's code introduced or can unbundle at the GitHub-scope level. There is no GitHub-native "grant read, add write later" flow available.

## What AUREM *can* still do about it (not GitHub-native, but real)

Confirmed via code search (`read_only`, `write_enabled`, `readonly_mode`, `repo_write_enabled` across `services/` and `routers/`): **no such gate exists today for GitHub repo access.** The hits that came back (`supabase_provisioner.py`, `loop_intent.py`) are an unrelated Supabase-database read-only/grace-period feature and a chat-intent classifier — neither touches GitHub write calls.

This means: even though the GitHub-issued token is write-capable the moment a repo is connected, AUREM's own tool-dispatch layer does not currently have an internal switch that would refuse to call a write-capable GitHub API method (file commit, push, PR merge) for a project the user wants read-only. Nothing in the code path prevents a write call today if the model decides to make one and the underlying token allows it — the ONLY thing standing in the way of an unwanted write today is that the model doesn't attempt one, not an enforced gate.

## Recommendation (not implemented in this task — approved as later work)
A **revocable internal write gate**, independent of the bundled GitHub scope:
- Add a per-project boolean (e.g., `repo_write_enabled`, default matching current behavior so this doesn't regress anyone) stored alongside the existing project/installation record.
- Enforce it at the single chokepoint where GitHub write operations are dispatched (the tool executor that calls the GitHub content-write endpoints), not scattered across callers.
- Expose a toggle so a user can flip a connected repo to "AUREM can read this repo but not write to it" without disconnecting/reinstalling the GitHub App.
- This does **not** change what GitHub grants AUREM (still bundled at the App level) — it adds AUREM's own enforcement on top, which is the only lever actually available given GitHub's permission model.

This recommendation is intentionally not implemented yet — it needs its own scoped change (new field, migration default, one enforcement chokepoint, tests) rather than being folded into this investigation.
