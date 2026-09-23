# GitHub repository missing from Projects — RCA and verification

Date: 2026-09-23. Scope: targeted code fix and preview/sandbox verification.

## Report and boundaries

On `https://auremcto.com/projects`, GitHub approval completed but a new project did not appear, even after refresh. Production page requires login; no production account trace or live GitHub consent was available. The following are **reproduced code paths explaining the symptom**, not a claim that the exact production session was observed.

No production settings, GitHub App permissions, user credentials, repository grants or provider configurations were changed. No runtime APIs were replaced by mocks.

## Root causes and fixes

| Cause | Observable consequence | Fix |
|---|---|---|
| Popup success recorded a fresh installation but left connecting/polling active | An unchanged existing grant kept the picker hidden and could later display denied | Explicit trusted callback completion stops polling, clears denial, invalidates stale continuations |
| Polling compared installation IDs and repository COUNTS only | Replacing one repository with another of equal count did not signal success | Compare sorted repository identities per installation |
| Repo synchronization stopped on global `state=connected` | An old connected repo stopped polling before the newly granted installation populated | Scope readiness to attempted installation IDs; bounded retry and visible failure |
| No-opener bridge routed to `/dashboard` | Existing users had no mounted onboarding wizard to finish adding the repo | Return to `/projects?flow=connect-repo…` and mount AddProjectWizard there |
| Wizard displayed only session-fresh installations | After refresh/reopen, already-authorized but unimported repos were inaccessible | Explicit user-triggered authorized-repo recovery; no silent stale selection |
| Project create handoff did not await list refresh and forced dashboard navigation | User did not receive a selected Projects entry; refresh failures lost context | Await list refresh, select new ID, stay on Projects, retain saved ID for refresh retry |

## Persistence contract

1. GitHub callback links installation metadata to the account.
2. Authenticated `/github/app/status` provides that account's repositories.
3. User explicitly selects/approves the project in the wizard.
4. `POST /api/aurem-dev/cto/projects/add` verifies installation ownership and GitHub repository access, then persists a project.
5. `GET /api/aurem-dev/cto/projects/list` returns only the current account's projects.

The callback intentionally does not automatically import every repository in a GitHub grant. UI now makes the remaining Add project confirmation visible. A page reload cannot create a project that was never saved.

## Other scoped corrections

- Preserve GitHub default branch instead of forcing `main`.
- Reject success-without-project-ID; show inline actionable API/refresh failures.
- On 409 already_connected, offer opening the existing saved project without another create.
- Prevent duplicate submits and closing mid-save.
- Accept popup messages only from expected API origin and the actual opened window; callback ID remains a hint, not authorization.
- Bridge uses exact same-origin postMessage target; errors rendered as text, not untrusted HTML.
- Modal portal/shared stacking/focus management prevent floating guide blocking mobile controls.

## Evidence

- Before fixes: four new frontend regression cases plus bridge return test failed.
- Final targeted frontend: **38 passed** across hook, wizard, Projects return, existing onboarding and reconnect suites.
- Final targeted backend: **57 passed**, including **2 REAL MongoDB tests**.
- Mongo tests exercise actual callback upsert, project creation/list routes and ownership verifier. Separate Mongo client confirms durable record with correct owner, installation and `develop` branch. Foreign account rejected; list isolated; duplicate create returns existing ID. Temporary DBs dropped.
- **MOCKED:** external GitHub metadata/repository/token responses and costly scans in Mongo tests; test-only identity resolver for ASGI route tests; deterministic GitHub/create/list browser fixtures in the independent test run.
- Actual external preview browser: real login, existing Projects page, wizard opening and pending return. Follow-up used login test IDs successfully (report's missing-selector concern not reproduced). Modal hit-testing and viewport bounds passed 320/390/768/1024/1440px.
- Build: `yarn build` passed. Focused ESLint passed. New Python tests Ruff-clean. GitHub router Ruff baseline/current both 27, zero new findings; no repository-wide clean-lint claim.
- Reports: `test_reports/iteration_391.json`, `test_reports/iteration_391_followup.json`.

## Remaining verification / backlog

P0: with a real authorized GitHub account, run popup and no-opener grant → select repository → Add project → list selected → reload persistence. Production-specific account attribution, provider callback delivery and installed version are not live-verified here.

P2: split legacy large project/connect modules separately. Consider an access / selection / save progress indicator.

Older production startup health and platform GitHub export/secrets issues were not addressed by this fix.