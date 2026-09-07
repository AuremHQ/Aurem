"""R10 real-GitHub E2E driver — pinned to the disposable testbed repo.

Phases (run in order, each idempotent-ish and logging to stdout):
  ship    — create auremcto/ship-* branch, commit one throwaway file,
            open draft PR labelled aura:ship, record loop_outcomes +
            loop_sessions exactly like loop_engine.confirm_ship does.
  merge   — mark PR ready, squash-merge it (simulates the user merging
            on GitHub), then deliver a signed pull_request webhook to the
            preview backend so merged_sha is captured.
  verify  — after the UI rollback: read GitHub main history, prove the
            revert commit is there and its parent chain contains merged_sha.
State is kept in /app/e2e-proof/R10-e2e/state.json.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

import httpx  # noqa: E402

OWNER, REPO, BASE = "polarisbuiltinc-wq", "aurem-rollback-testbed", "main"
INSTALL_ID = 152797252
USER_EMAIL = "test@aurem.dev"
PROJECT_ID = "p_r10_testbed_e2e"
STATE_DIR = "/app/e2e-proof/R10-e2e"
STATE = f"{STATE_DIR}/state.json"
GH = "https://api.github.com"


def _load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def _save(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(d, open(STATE, "w"), indent=1)


async def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.github_app_config import ensure_configured_from_db
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    assert await ensure_configured_from_db(db), "GitHub App config missing"
    return db


async def _token():
    from services.github_app import get_installation_token
    tok, _ = await get_installation_token(INSTALL_ID)
    return tok


def _h(tok):
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}


async def _head_sha(c, tok):
    r = await c.get(f"{GH}/repos/{OWNER}/{REPO}/git/refs/heads/{BASE}", headers=_h(tok))
    r.raise_for_status()
    return r.json()["object"]["sha"]


async def ship():
    db = await _db()
    tok = await _token()
    async with httpx.AsyncClient(timeout=30) as c:
        # Safety: the installation token must see the testbed repo, and we
        # never touch anything else.
        r = await c.get(f"{GH}/repos/{OWNER}/{REPO}", headers=_h(tok))
        r.raise_for_status()
        assert r.json()["full_name"] == f"{OWNER}/{REPO}"
        sha_before = await _head_sha(c, tok)
    print("main HEAD before ship:", sha_before)

    user = await db.dev_users.find_one({"email": USER_EMAIL}, {"user_id": 1})
    uid = user["user_id"]
    await db.cto_projects.update_one(
        {"project_id": PROJECT_ID},
        {"$set": {"project_id": PROJECT_ID, "user_id": uid,
                  "name": f"{OWNER}/{REPO}", "github_owner": OWNER, "github_repo": REPO,
                  "github_url": f"https://github.com/{OWNER}/{REPO}",
                  "auth_method": "github_app", "installation_id": str(INSTALL_ID),
                  "installation_active": True, "branch": BASE, "default_branch": BASE,
                  "status": "connected", "tech_stack": "auto",
                  "created_at": time.time()}},
        upsert=True,
    )

    from services.loop_safety import (ship_branch_name, create_or_reuse_branch,
                                      open_draft_pr, add_pr_label)
    from services.github_api_writer import commit_files
    from services import loop_outcomes as lo

    loop_id = f"r10e2e_{uuid.uuid4().hex[:10]}"
    branch = ship_branch_name("r10e2e")
    ok, err = await create_or_reuse_branch(owner=OWNER, repo=REPO, base_branch=BASE,
                                           new_branch=branch, token=tok)
    assert ok, err
    ts = datetime.now(timezone.utc).isoformat()
    path = f"r10-e2e/{loop_id}.md"
    res = await commit_files(
        owner=OWNER, repo=REPO, branch=branch, token=tok,
        files={path: f"# R10 E2E\n\nloop={loop_id}\nshipped_at={ts}\n"},
        commit_message=f"ship: R10 E2E throwaway change ({loop_id})",
        author_name="AUREM", author_email="cto@aurem.dev",
    )
    full_sha = res.get("full_sha") or res.get("sha")
    print("ship commit on branch:", branch, full_sha)
    pr_url, pr_err = await open_draft_pr(
        owner=OWNER, repo=REPO, head_branch=branch, base_branch=BASE,
        title=f"ship: R10 E2E ({loop_id})",
        body=f"Opened by ORA (Loop {loop_id}).\n\nCommit: {full_sha}", token=tok)
    assert pr_url, pr_err
    pr_number = int(pr_url.rsplit("/", 1)[-1])
    await add_pr_label(owner=OWNER, repo=REPO, pr_number=pr_number, label="aura:ship", token=tok)
    print("PR opened:", pr_url)

    await lo.record_shipped_commit(
        db, loop_id=loop_id, task_id=None, user_id=uid, project_id=PROJECT_ID,
        commit_sha=full_sha, file_paths=[path], owner=OWNER, repo=REPO, branch=BASE,
        pr_url=pr_url, pr_number=pr_number, ship_branch=branch)
    await db.loop_sessions.insert_one({
        "loop_id": loop_id, "user_id": uid, "project_id": PROJECT_ID,
        "state": "completed", "created_at": ts, "updated_at": datetime.now(timezone.utc),
        "user_message": "R10 E2E throwaway change",
        "context": {"commit": {
            "message": f"ship: R10 E2E ({loop_id})", "sha": full_sha[:7], "full_sha": full_sha,
            "html_url": f"https://github.com/{OWNER}/{REPO}/commit/{full_sha}",
            "files": [path], "pr_url": pr_url, "pr_number": pr_number,
            "pr_branch": branch, "expected_branch_head_sha": full_sha}},
    })
    _save({"loop_id": loop_id, "branch": branch, "pre_merge_sha": full_sha,
           "pr_url": pr_url, "pr_number": pr_number, "sha_before": sha_before,
           "path": path, "user_id": uid})
    print("state saved:", STATE)


async def merge():
    st = _load()
    db = await _db()
    tok = await _token()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{GH}/repos/{OWNER}/{REPO}/pulls/{st['pr_number']}", headers=_h(tok))
        r.raise_for_status()
        node_id = r.json()["node_id"]
        g = await c.post("https://api.github.com/graphql", headers=_h(tok), json={
            "query": 'mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{isDraft}}}',
            "variables": {"id": node_id}})
        print("mark ready:", g.status_code, g.json().get("data"))
        m = await c.put(f"{GH}/repos/{OWNER}/{REPO}/pulls/{st['pr_number']}/merge",
                        headers=_h(tok), json={"merge_method": "squash",
                                               "commit_title": f"ship: R10 E2E ({st['loop_id']}) (#{st['pr_number']})"})
        print("merge:", m.status_code, m.json().get("merged"), m.json().get("sha"))
        assert m.json().get("merged"), m.text
        r = await c.get(f"{GH}/repos/{OWNER}/{REPO}/pulls/{st['pr_number']}", headers=_h(tok))
        pr = r.json()
        repo = (await c.get(f"{GH}/repos/{OWNER}/{REPO}", headers=_h(tok))).json()
    merged_sha = pr["merge_commit_sha"]
    print("GitHub merge_commit_sha:", merged_sha, "(pre-merge head:", st["pre_merge_sha"], ")")

    # Deliver the real-shaped pull_request webhook, HMAC-signed, to the preview backend.
    from services.github_app_config import get_runtime_github_app_config
    secret = get_runtime_github_app_config()["webhook_secret"].encode()
    payload = {"action": "closed", "number": pr["number"], "pull_request": pr,
               "repository": repo, "installation": {"id": INSTALL_ID}}
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    base = os.environ.get("PREVIEW_API", "http://localhost:8001")
    async with httpx.AsyncClient(timeout=30) as c:
        w = await c.post(f"{base}/api/aurem-dev/github/app/webhook", content=raw, headers={
            "Content-Type": "application/json", "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": f"r10e2e-{uuid.uuid4()}"})
    print("webhook delivered:", w.status_code, w.text[:200])
    row = await db.loop_outcomes.find_one({"loop_id": st["loop_id"]}, {"_id": 0, "merged_sha": 1, "pr_status": 1, "commit_sha": 1})
    print("loop_outcomes row:", row)
    assert row["pr_status"] == "merged" and row["merged_sha"] == merged_sha, row
    assert row["merged_sha"] != row["commit_sha"], "squash merge must differ from pre-merge head"
    st.update({"merged_sha": merged_sha, "sha_after_merge": merged_sha})
    _save(st)
    print("t_ship_sha_captured_on_merge (live): PASS")


async def verify():
    st = _load()
    db = await _db()
    tok = await _token()
    sess = await db.loop_sessions.find_one({"loop_id": st["loop_id"]},
                                           {"_id": 0, "rollback_status": 1, "rollback_sha": 1,
                                            "rollback_verified": 1, "rollback_commit_sha": 1,
                                            "rollback_html_url": 1})
    print("loop_sessions rollback fields:", sess)
    async with httpx.AsyncClient(timeout=30) as c:
        head = await _head_sha(c, tok)
        r = await c.get(f"{GH}/repos/{OWNER}/{REPO}/commits", headers=_h(tok),
                        params={"sha": BASE, "per_page": 10})
        commits = r.json()
    shas = [x["sha"] for x in commits]
    print("main history (newest first):")
    for x in commits[:6]:
        print("  ", x["sha"][:12], "|", x["commit"]["message"].splitlines()[0][:80])
    revert_full = None
    for x in commits:
        if x["sha"].startswith(sess.get("rollback_sha") or "~~"):
            revert_full = x["sha"]
    assert sess.get("rollback_status") == "done", sess
    assert revert_full, "revert commit not in main history"
    assert head == revert_full, f"main HEAD {head} != revert {revert_full}"
    assert st["merged_sha"] in shas, "merged_sha vanished from history (revert must not rewrite)"
    assert shas.index(revert_full) < shas.index(st["merged_sha"])
    assert sess.get("rollback_commit_sha") == st["merged_sha"], "rollback targeted the wrong sha"
    # The reverted tree must no longer contain the shipped file.
    async with httpx.AsyncClient(timeout=30) as c:
        f = await c.get(f"{GH}/repos/{OWNER}/{REPO}/contents/{st['path']}", headers=_h(tok), params={"ref": BASE})
    print("shipped file on main after revert -> HTTP", f.status_code, "(404 expected)")
    assert f.status_code == 404
    st.update({"revert_sha": revert_full, "sha_after_rollback": head})
    _save(st)
    print(json.dumps({"sha_before_ship": st["sha_before"], "pre_merge_head": st["pre_merge_sha"],
                      "merged_sha": st["merged_sha"], "revert_sha": revert_full,
                      "main_head_after_rollback": head}, indent=1))
    print("t_rollback_reverts_merged_sha (live): PASS")


if __name__ == "__main__":
    asyncio.run({"ship": ship, "merge": merge, "verify": verify}[sys.argv[1]]())
