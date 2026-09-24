import asyncio, json, os, sys, time
sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

import httpx
from services import github_app as ga
from services import loop_safety as ls

OWNER = "polarisbuiltinc-wq"
REPO = "ora-grounding"
INSTALL_ID = 152797252
OUT = "/app/e2e-proof/R2-live-drill-ora-grounding"
GH = "https://api.github.com"


def dump(name, obj):
    with open(f"{OUT}/{name}", "w") as f:
        json.dump(obj, f, indent=2, default=str)
    print(f"wrote {name}")


async def main():
    from services.github_app_config import ensure_configured_from_db
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    ok = await ensure_configured_from_db(db)
    print("github app config hydrated:", ok)

    # 01 — installation health
    installs = await ga.list_installations()
    row = next((i for i in installs if i.get("id") == INSTALL_ID), None)
    dump("01_installation_ok.json", {"found": row is not None, "installation": row})
    assert row is not None, "installation not found in live list"

    # token
    token, _expiry = await ga.get_installation_token(INSTALL_ID)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(timeout=20) as client:
        # 02 — repo ok
        r = await client.get(f"{GH}/repos/{OWNER}/{REPO}", headers=headers)
        dump("02_repo_ok.json", {"status": r.status_code, "body": r.json()})
        assert r.status_code == 200, r.text

        # 03 — base sha (main, pre-drill)
        r = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/main", headers=headers)
        dump("03_base_sha.json", {"status": r.status_code, "body": r.json()})
        assert r.status_code == 200, r.text
        base_sha = r.json()["object"]["sha"]
        print("BASE_SHA:", base_sha)

        # ── SHIP #1 — real fix, open PR, merge (proves ship -> live) ──
        ts = int(time.time())
        branch1 = ls.ship_branch_name(f"r2drill-merge-{ts}")
        ok, err = await ls.create_or_reuse_branch(
            owner=OWNER, repo=REPO, base_branch="main",
            new_branch=branch1, token=token,
        )
        assert ok, err

        # a real, trivial, obviously-a-drill commit via contents API
        content = f"R2 live drill — ship-via-PR merge test\nrun_at={ts}\n".encode()
        import base64
        put = await client.put(
            f"{GH}/repos/{OWNER}/{REPO}/contents/AUREM_R2_DRILL.md",
            headers=headers,
            json={
                "message": "ship: AUREM R2 live drill (merge path)",
                "content": base64.b64encode(content).decode(),
                "branch": branch1,
            },
        )
        dump("03b_ship1_commit.json", {"status": put.status_code, "body": put.json()})
        assert put.status_code in (200, 201), put.text

        pr_url, pr_err = await ls.open_draft_pr(
            owner=OWNER, repo=REPO, head_branch=branch1, base_branch="main",
            title="ship: AUREM R2 live drill (merge path)",
            body="Automated R2 live drill — will be merged then left in repo history as proof.",
            token=token,
        )
        assert pr_err is None, pr_err
        pr_number1 = int(pr_url.rstrip("/").split("/")[-1])
        dump("04_pr_open.json", {"pr_url": pr_url, "pr_number": pr_number1, "branch": branch1})
        print("PR1 OPENED:", pr_url)

        label_ok, label_err = await ls.add_pr_label(
            owner=OWNER, repo=REPO, pr_number=pr_number1, label="aura:ship", token=token,
        )
        dump("04b_pr_label.json", {"ok": label_ok, "err": label_err})

        # merge
        m = await client.put(
            f"{GH}/repos/{OWNER}/{REPO}/pulls/{pr_number1}/merge",
            headers=headers,
            json={"merge_method": "squash"},
        )
        dump("05_pr_merged.json", {"status": m.status_code, "body": m.json()})
        assert m.status_code == 200, m.text
        merge_sha = m.json().get("sha")
        print("PR1 MERGED, merge_sha:", merge_sha)

        # delete the merged branch (GitHub doesn't auto-delete)
        await client.delete(f"{GH}/repos/{OWNER}/{REPO}/git/refs/heads/{branch1}", headers=headers)

        # confirm new main sha
        r = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/main", headers=headers)
        post_merge_sha = r.json()["object"]["sha"]
        dump("05b_main_after_merge.json", {"sha": post_merge_sha})
        print("MAIN AFTER MERGE:", post_merge_sha)

        # ── SHIP #2 — open PR, then discard (proves clean rollback) ──
        branch2 = ls.ship_branch_name(f"r2drill-discard-{ts}")
        ok, err = await ls.create_or_reuse_branch(
            owner=OWNER, repo=REPO, base_branch="main",
            new_branch=branch2, token=token,
        )
        assert ok, err
        content2 = f"R2 live drill — ship-via-PR ROLLBACK test (should never merge)\nrun_at={ts}\n".encode()
        put2 = await client.put(
            f"{GH}/repos/{OWNER}/{REPO}/contents/AUREM_R2_DRILL_ROLLBACK.md",
            headers=headers,
            json={
                "message": "ship: AUREM R2 live drill (rollback path, will be discarded)",
                "content": base64.b64encode(content2).decode(),
                "branch": branch2,
            },
        )
        assert put2.status_code in (200, 201), put2.text

        pr_url2, pr_err2 = await ls.open_draft_pr(
            owner=OWNER, repo=REPO, head_branch=branch2, base_branch="main",
            title="ship: AUREM R2 live drill (rollback path)",
            body="Will be closed unmerged + branch deleted to prove clean rollback.",
            token=token,
        )
        assert pr_err2 is None, pr_err2
        pr_number2 = int(pr_url2.rstrip("/").split("/")[-1])
        print("PR2 OPENED:", pr_url2)

        # pre-rollback sha (must equal post-rollback sha — no residue)
        r = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/main", headers=headers)
        pre_rollback_sha = r.json()["object"]["sha"]

        result = await ls.close_and_retract(
            owner=OWNER, repo=REPO, pr_number=pr_number2, branch=branch2, token=token,
        )
        dump("06_pr_close.json", {"pr_number": pr_number2, "branch": branch2, "result": result})
        print("CLOSE_AND_RETRACT:", result)

        # 07 — confirm branch actually gone
        br_check = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/{branch2}", headers=headers)
        dump("07_branch_deleted.json", {"status": br_check.status_code,
                                          "expected_404": br_check.status_code == 404})
        print("BRANCH2 STILL EXISTS?", br_check.status_code != 404)

        # 09 — post state: main sha unaffected by the discarded PR
        r = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/main", headers=headers)
        post_state_sha = r.json()["object"]["sha"]
        dump("09_post_state.json", {
            "pre_drill_sha": base_sha,
            "post_merge_sha": post_merge_sha,
            "pre_rollback_sha": pre_rollback_sha,
            "post_rollback_sha": post_state_sha,
            "rollback_left_no_residue": pre_rollback_sha == post_state_sha,
        })
        print("PRE-ROLLBACK SHA:", pre_rollback_sha)
        print("POST-ROLLBACK SHA:", post_state_sha)
        print("NO RESIDUE:", pre_rollback_sha == post_state_sha)

        with open(f"{OUT}/pr_numbers.txt", "w") as f:
            f.write(f"pr1_merged={pr_number1}\npr2_discarded={pr_number2}\n")


asyncio.run(main())
