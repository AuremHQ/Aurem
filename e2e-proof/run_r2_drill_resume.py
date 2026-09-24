import asyncio, json, os, sys, time, base64
sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

import httpx
from services import github_app as ga
from services import loop_safety as ls
from motor.motor_asyncio import AsyncIOMotorClient
from services.github_app_config import ensure_configured_from_db

OWNER = "polarisbuiltinc-wq"
REPO = "ora-grounding"
INSTALL_ID = 152797252
OUT = "/app/e2e-proof/R2-live-drill-ora-grounding"
GH = "https://api.github.com"
PR_NUMBER1 = 3
BRANCH1 = "auremcto/ship-r2drill-merge-resume"


def dump(name, obj):
    with open(f"{OUT}/{name}", "w") as f:
        json.dump(obj, f, indent=2, default=str)
    print(f"wrote {name}")


async def main():
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    await ensure_configured_from_db(db)
    token, _ = await ga.get_installation_token(INSTALL_ID)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(timeout=20) as client:
        # merge PR #3 (now ready-for-review, not draft)
        m = await client.put(
            f"{GH}/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER1}/merge",
            headers=headers,
            json={"merge_method": "squash"},
        )
        dump("05_pr_merged.json", {"status": m.status_code, "body": m.json()})
        assert m.status_code == 200, m.text
        merge_sha = m.json().get("sha")
        print("PR1 MERGED, merge_sha:", merge_sha)
        actual_branch1 = (await client.get(f"{GH}/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER1}", headers=headers)).json()["head"]["ref"]

        await client.delete(f"{GH}/repos/{OWNER}/{REPO}/git/refs/heads/{actual_branch1}", headers=headers)

        r = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/main", headers=headers)
        post_merge_sha = r.json()["object"]["sha"]
        dump("05b_main_after_merge.json", {"sha": post_merge_sha})
        print("MAIN AFTER MERGE:", post_merge_sha)

        # ── SHIP #2 — open PR, then discard (proves clean rollback) ──
        ts = int(time.time())
        branch2 = ls.ship_branch_name(f"r2drill-discard-{ts}")
        ok, err = await ls.create_or_reuse_branch(
            owner=OWNER, repo=REPO, base_branch="main",
            new_branch=branch2, token=token,
        )
        assert ok, err
        content2 = f"R2 live drill - ship-via-PR ROLLBACK test (should never merge)\nrun_at={ts}\n".encode()
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
        dump("04_pr_open_ship2.json", {"pr_url": pr_url2, "pr_number": pr_number2, "branch": branch2})
        print("PR2 OPENED:", pr_url2)

        r = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/main", headers=headers)
        pre_rollback_sha = r.json()["object"]["sha"]

        result = await ls.close_and_retract(
            owner=OWNER, repo=REPO, pr_number=pr_number2, branch=branch2, token=token,
        )
        dump("06_pr_close.json", {"pr_number": pr_number2, "branch": branch2, "result": result})
        print("CLOSE_AND_RETRACT:", result)

        br_check = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/{branch2}", headers=headers)
        dump("07_branch_deleted.json", {"status": br_check.status_code,
                                          "expected_404": br_check.status_code == 404})
        print("BRANCH2 STILL EXISTS?", br_check.status_code != 404)

        r = await client.get(f"{GH}/repos/{OWNER}/{REPO}/git/ref/heads/main", headers=headers)
        post_state_sha = r.json()["object"]["sha"]

        base = json.load(open(f"{OUT}/03_base_sha.json"))["body"]["object"]["sha"]
        dump("09_post_state.json", {
            "pre_drill_sha": base,
            "post_merge_sha": post_merge_sha,
            "pre_rollback_sha": pre_rollback_sha,
            "post_rollback_sha": post_state_sha,
            "rollback_left_no_residue": pre_rollback_sha == post_state_sha,
        })
        print("PRE-ROLLBACK SHA:", pre_rollback_sha)
        print("POST-ROLLBACK SHA:", post_state_sha)
        print("NO RESIDUE:", pre_rollback_sha == post_state_sha)

        with open(f"{OUT}/pr_numbers.txt", "w") as f:
            f.write(f"pr1_merged={PR_NUMBER1}\npr2_discarded={pr_number2}\nmerge_sha={merge_sha}\n")


asyncio.run(main())
