# PR A — Security: public error-report hardening + autofix safety

**Scope:** items 1 & 2 only (backend-only, no UI). Ships as its own
branch/PR, separate from PR B and from the "Fitness-function
invariants" carry-over work.

**Repo state this was built against:** local `main` @ `2c8947e2`, with
`claude/eloquent-goodall-2cty9y` (PR #190, **still open, not merged**
at commit `15033a93`) reconciled in-place — see "Conflict with PR
#190" below. Re-check PR #190's merge status before opening this PR;
if it has since merged, diff this branch against the new main tip for
`services/admin_error_autofix.py` / `routers/admin_support.py` once
more before requesting review.

## Item 1 — `POST /api/aurem-dev/admin/errors/report` (`routers/admin_public.py`)

Was unauthenticated (by design — logged-out users hit console errors
too), exempt from the global rate limiter, and had no per-IP throttle,
no cap on distinct rows created, and no `type` allowlist. Fixed,
staying unauthenticated and always HTTP 200 (the frontend
ErrorBoundary/console hook retries on failure, so a 429 would storm it
harder, not calm it):
- Per-IP cap, 30/min (`services.rate_limiter`). Over cap → `{"ok":
  false, "error": "rate_limited"}`, HTTP 200.
- Global cap on **new distinct rows created in the trailing hour**
  (200/hr) — repeat-reporting an existing `(message, url)` stays free;
  counted via the Mongo `_id`'s embedded timestamp, no new index/field
  needed. Over cap → same `{"ok": false, "error": "rate_limited"}`.
- `type` allowlisted to the 3 values `errorReporter.js` actually sends
  (`console_error`, `unhandled_rejection`, `window_error`); anything
  else is coerced to `console_error` rather than rejected.
- Field caps lowered: message 4000→1000, stack 16000→4000, url
  1000→400.
- Every row now stamped `trusted: false` (this sink has no auth, so
  nothing it writes is ever admin-originated data).
- Existing 90-day TTL index on `last_seen_at` is untouched.

Tests: `backend/tests/test_regression_iter9_admin_errors_report_hardening.py`
(6 tests — burst-then-limited, retry-path-always-200, type coercion,
trusted flag, field caps, hourly new-row cap).

## Item 2 — Autofix safety (`services/admin_error_autofix.py`, `routers/admin_support.py`)

**Also fixes a bug found while implementing this: the autofix feature
has never actually worked.** The pre-existing call to
`chat_with_tools()` passed `history_lines=[]` (not a real parameter of
that function) and omitted the required `jwt_token` — every autofix
click raised a `TypeError` before ever reaching the LLM. This was true
on `main` and is still true on PR #190's branch tip. There is also
**no frontend UI** anywhere in the codebase that calls
`GET /admin/errors` or `POST /admin/errors/{id}/autofix*` — the whole
feature is backend-only and was unreachable in practice. Whether to
build a UI for it, or to keep the feature at all, is a separate
product decision — not addressed by this PR.

Given that, the actual security fix here is about what happens **the
first time someone does wire this up** (a UI, a script, a curl
command from an admin session):

- The admin-triggered agent used to run in `mode="maxx"` with full
  write/ship tools, fed attacker-controlled report text (the report
  sink above has no auth). Framing that text as untrusted data in the
  prompt (which PR #190 already does — kept verbatim, see below) is
  only a partial mitigation: an LLM can still be talked into treating
  "data" as instructions.
- Split into two phases:
  - **Propose** (`POST /errors/{id}/autofix`): runs with
    `force_read_only=True`. `services/local_tools.py`'s
    `invoke_local_tool()` now takes this as an explicit ctx flag and
    blocks `write_repo_file`/`patch_repo_file` **unconditionally**,
    checked before `args` are even inspected — this can't be argued
    around by prompt content, unlike the existing prompt-wording-based
    `read_only_request()` heuristic it sits in front of. Ends in
    `autofix_status="proposed"` with the agent's own fix description
    in `autofix_proposal`. Nothing is written.
  - **Apply** (`POST /errors/{id}/autofix/apply`, new): the only path
    that can result in a write. Refuses with 409 unless
    `autofix_status=="proposed"` — i.e. a human must have already
    triggered Propose and had something to read. Re-runs
    `chat_with_tools()` **without** `force_read_only`, quoting back
    exactly the already-reviewed proposal.
  - **Reject** (`POST /errors/{id}/autofix/reject`, new): discards a
    pending proposal without applying it.
- `GET /errors` now also returns `trusted` and `autofix_proposal` per
  row, ready for whatever UI eventually consumes this.

### Conflict with PR #190 (`claude/eloquent-goodall-2cty9y`, open, not merged)

That branch added `_FENCE`/`_FENCE_END`/`_scrub`/`build_autofix_prompt`
to `services/admin_error_autofix.py` (the prompt-fencing partial
mitigation) and `tests/test_regression_iter396_autofix_prompt_treats_report_as_untrusted.py`
asserting on them. Fetched via the GitHub API (no local remote is
configured in this sandbox — see below) and reconciled by hand:
`_FENCE`, `_FENCE_END`, `_scrub`, and `build_autofix_prompt` are kept
**verbatim** from that branch; this PR's `force_read_only`/propose-
apply-reject split and the `jwt_token` fix are layered on top of it.
`test_regression_iter396_*` was verified against this reconciled file
directly (not just by inspection) and its 6 assertions pass unchanged.
PR #190's own `run_error_autofix()` still has the `history_lines`/
missing-`jwt_token` bug and is NOT read-only-gated — if that branch
merges to main before this PR is reviewed, re-diff
`services/admin_error_autofix.py` and `routers/admin_support.py`
once more; the reconciliation above should already cover it but a
second pass on the real merged main costs little.

Tests: `backend/tests/test_regression_iter10_admin_errors_autofix_safety.py`
(9 tests — write tools blocked unconditionally incl. with hostile
prompt-injection text in the args, read tools unaffected, propose
phase always sets `force_read_only=True` + fences the report text,
apply phase intentionally does not, apply/reject endpoints gate on
existing proposal state, list_errors exposes the new fields).

## No git remote in this sandbox

`git remote -v` is empty here — only local `main` +
`extract/ora-grounding-opensource`. Branch/PR/merge state above (PR
#190 open @ `15033a93`, `main` @ `2c8947e2`, zero commits behind) and
the two files' actual content were fetched live via this repo's own
installed GitHub App (`services/github_app.get_token_for_repo`), not
via `git fetch` — there is no configured `origin` to fetch from in
this environment. All 15 new tests above were run against real code,
not guessed. This PR's diff is committed locally on `main`; please use
"Save to GitHub" to push it as its **own branch**, separate from
`main` and from PR #190 — do not stack PR B or the invariants work on
top of it.
