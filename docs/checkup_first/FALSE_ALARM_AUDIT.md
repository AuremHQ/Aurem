# Checkup false-alarm audit (read-only)

A false alarm costs more trust than a miss, so before a check is trusted we run the
whole checkup over real public repos and read every critical/high finding by hand.

## Run it (needs only `git` and the backend Python deps; no network, no tokens)

```bash
git clone --depth 20 https://github.com/vercel/nextjs-subscription-payments.git
git clone --depth 20 https://github.com/fastapi/full-stack-fastapi-template.git
git clone --depth 1 --filter=blob:none --sparse https://github.com/supabase/supabase.git \
  && (cd supabase && git sparse-checkout set examples/user-management)

cd backend   # inside this repo
python scripts/checkup_false_alarm_audit.py ../../nextjs-subscription-payments HEAD
python scripts/checkup_false_alarm_audit.py ../../full-stack-fastapi-template HEAD
python scripts/checkup_false_alarm_audit.py ../../supabase HEAD:examples/user-management/react-user-management
python scripts/checkup_false_alarm_audit.py .. origin/main        # our own repo
```

Add `--json out.json` to keep the full list. The script prints counts per rule and every
critical/high finding with `file:line`; secret values are never printed.

## Reference repos (stack coverage)

| Stack | Repo | Pinned commit when first audited |
|---|---|---|
| Next.js + Stripe + Supabase | `vercel/nextjs-subscription-payments` | `bdd0813206e47e6b218d42f15a7976c8a0d3c3eb` |
| FastAPI + Postgres + React | `fastapi/full-stack-fastapi-template` | `cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7` |
| React + Supabase | `supabase/supabase` -> `examples/user-management/react-user-management` | `be976bec49da3c92d030a526e7916688449a2638` |

Use `git checkout <sha>` to reproduce the numbers below; newer commits will differ.

## How to judge a finding

1. Open the file at the reported line. Is the problem real in that project?
2. Real but intended (for example a template that ships a placeholder `.env`)? Count it as
   *true but low value* and note it; do not call it a false alarm.
3. Not a problem at all, or the evidence is wrong? That is a **false alarm**: write a
   regression test (`test_regression_iter<N>_...`) before changing the check.

## Log

Add a row per audit run (date, who, repo + sha, findings by rule, false alarms found, test added).
