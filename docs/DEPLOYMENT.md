# AUREM CTO — Deployment Guide

> **Emergent is OFF.** The "Emergent Dashboard → Re-deploy" path below is
> historical. Until the owner confirms otherwise, treat the GitHub flow in
> "How a deploy happens now" as the only deploy path.

## How a deploy happens now

1. A PR is merged into `main` (needs green CI + Nikhat's review + the owner's merge click).
2. The push to `main` starts **AUREM Auto-Deploy** (`.github/workflows/auto_deploy.yml`):
   - `gate-on-ci` waits up to 30 min for **AUREM CI — Build + Test Guard** and
     **Quality Gate — Bug-fix Discipline** to both be `success` on the *same commit*.
     Anything else (failure, timeout) blocks the deploy (fail-closed).
   - `deploy-and-report` then **stops and waits for approval** (see below).
3. After a reviewer approves, the job POSTs the repo secret `AUREM_DEPLOY_HOOK_URL`
   and reports the **real** HTTP result to `/api/aurem-dev/github-deploy/report`.
   "Success" there means *the hook accepted the request (HTTP 2xx)* — not that the
   site is healthy. Always confirm with the health check below.
4. `deploy.yml` ("Auto Deploy to Production") is a manual `workflow_dispatch` that calls
   `VERCEL_DEPLOY_HOOK`; it is behind the same approval.

Nothing else deploys or reports a deploy: `auto_push.yml` and the `deploy-gate` job in
`ci.yml` used to post a "success" deploy report on every green/pushed commit even though
nothing was deployed; they no longer do.

## Who approves

Every job that touches a production deploy hook runs in the GitHub **Environment `production`**
with **required reviewers**. Only the owner (`polarisbuiltinc-wq`) is a required
reviewer. A deploy shows as "Waiting for review" on the workflow run; the owner opens the run,
checks the commit, and clicks **Review deployments → production → Approve and deploy**
(or **Reject**). No approval = no deploy.

### One-time setup (repo settings — done by the owner, not by code)

Settings → Environments → **New environment** → name exactly `production`:
- **Required reviewers**: add the owner. Tick **Prevent self-review** only if a second
  reviewer exists, otherwise leave it off (a solo owner could never approve their own push).
- **Deployment branches and tags**: *Selected branches* → `main` only.
- Do **not** add the deploy secrets to the environment unless you also remove them from
  repo secrets; keeping them as repo secrets is fine because the job is still gated.
- No wait timer is needed.

If the `production` environment does not exist yet, GitHub creates it on first use **with no
reviewers, i.e. no approval**. So create it with the reviewer *before* anything on `main`
can go green.

## Health check after every deploy

`curl -s https://auremcto.com/api/health | jq .` — compare `build_hash` with the commit
you approved. A deploy is "done" only when they match.

## Rollback

> UNVERIFIED: what `AUREM_DEPLOY_HOOK_URL` points at has not been confirmed, and with
> Emergent off the exact rollback mechanism depends on it. The steps below are the
> repo-side part that is true regardless; the owner must fill in the platform step.

1. Open a PR that **reverts** the bad merge commit (`git revert -m 1 <merge-sha>`) — do not
   force-push `main`.
2. Merge it after green CI. Auto-Deploy starts again and waits for approval.
3. Approve the deploy; confirm `build_hash` with the health check.
4. Platform step (owner to document here): _how the hook target redeploys a previous commit
   without waiting for CI, if CI is red during an incident._
5. Feature flags (`feature_flags` collection, admin → Settings) can switch a new feature
   off without a deploy: this is the fastest mitigation for anything flag-gated.

---

# Historical (Emergent-era) notes

## Current Setup
- Platform: Emergent
- Production: auremcto.com
- Health check: /api/health
- Redeploy: Emergent Dashboard → Manage Deployments → Re-deploy

## Environment Variables (Emergent Secrets)
- REACT_APP_BACKEND_URL = https://auremcto.com
- CORS_ORIGINS = https://auremcto.com,https://www.auremcto.com
- APP_URL = https://auremcto.com

## Blue-Green (Ready when needed)
Scripts in /app/scripts/:
- blue_green_switch.sh — health check before switching
- rollback.sh — emergency rollback guide

## Staging (When ready — no cost until activated)
Config: .emergent/staging.yml
URL: staging.auremcto.com
Cost: Same as prod pod (~$20/month)

## Feature Flags
Admin: auremcto.com/admin (Settings tab)
Feature flags in MongoDB: feature_flags collection

## Cost by Scale
Now:        $0 extra
100 users:  Add staging ($20/month)
1000 users: Blue-Green ($40/month total)
