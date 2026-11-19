# RCA — Deploy run d9fc8893-2c2c-4eb6-8a85-b1b4a8ecb39e (launch-pad-237)

## Outcome
Deploy FAILED at the `deploy_k8s` step. The backend container crash-looped on boot (CrashLoopBackOff, lastExit=1/Error, 3 restarts) and never became ready. The app's previously-live version (run e92c92ce) is still serving `https://launch-pad-237.emergent.host` — production is NOT down.

## Root cause — backend source SyntaxError
`backend/routers/wrapped.py` fails to import at startup, which aborts `main.py` (imported at `main.py:95`) and kills uvicorn before the backend can bind.

Python interpreter error from the pod boot:
```
File "/app/backend/routers/wrapped.py", line 67
    9            "completed_at": {"$gte": start_ts, "$lte": end_ts},  # shipped in period, not created in period
    ^
SyntaxError: ':' expected after dictionary key
```

Line 67 has a stray literal `9` prefixed to the dictionary entry inside the `db.cto_tasks.find({...})` filter. The `9` turns the line into invalid Python, so the whole module fails to compile.

### Current (broken) — backend/routers/wrapped.py line ~64-69
```python
    tasks = await db.cto_tasks.find(
        {
            "user_id":    user_id,
9            "completed_at": {"$gte": start_ts, "$lte": end_ts},  # shipped in period, not created in period
        },
        {"_id": 0},
    ).to_list(500)
```

### Fix — remove the stray `9`
```python
    tasks = await db.cto_tasks.find(
        {
            "user_id":    user_id,
            "completed_at": {"$gte": start_ts, "$lte": end_ts},  # shipped in period, not created in period
        },
        {"_id": 0},
    ).to_list(500)
```

## Verification
Reproduced in an eph sandbox from this run's exact source:
- `python3 -m py_compile backend/routers/wrapped.py` → **SyntaxError at line 67** (matches production crash).
- After removing the stray `9` prefix → `py_compile` returns **EXIT=0** (compiles cleanly).

## Who fixes it
E1 (main agent): apply the one-line edit above to `backend/routers/wrapped.py`, then redeploy. No manifest, secret, tier, or infra change is involved.
