---
status: draft
approved_by: null
approved_on: null
inject_on: [fix, bug, error, null, undefined, crash, exception, broken, not working, xss, sql injection, injection, sanitiz, mitigation, security pattern, auth guard]
max_chars: 1800
---

# Common fix patterns

General, behavior-level patterns only — never a specific user's repo,
file path, or incident.

- **Null/undefined access**: add a guard (`if (x == null) return;` /
  `x?.optionalChaining`) at the point of use, not a broad try/except that
  hides the real failure.
- **SQL injection**: replace string-formatted queries with parameterized
  queries / an ORM query builder. Never string-concatenate user input
  into a query.
- **Reflected/stored XSS**: escape or sanitize untrusted input before it
  reaches the DOM; never use `dangerouslySetInnerHTML`/`innerHTML` on
  unsanitized, user- or repo-derived text.
- **Missing auth guard**: add the existing auth/permission check used
  elsewhere in the same router, rather than inventing a new pattern.
- **Rollback/revert requests**: use the mutation-verb vocabulary the
  system already recognizes (revert, rollback, undo, restore) — these
  map to real handoff/ship paths; do not invent new verbs.

## Answering a "why didn't my fix ship" question

Check, in order: did a security/lint/test gate reject it, did the
fix-quota/entitlement gate block it, or did the fence/brief fail one of
the handoff-extraction gates (length, question mark, permission phrase,
read-only-only, missing mutation verb, missing file path, fabricated
citation). State which one, if known; otherwise say unknown.
