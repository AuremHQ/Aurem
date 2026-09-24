"""R10 live-Mongo dispatcher test — throwaway outcomes rows.

Exercises services.loop_safety.dispatch_pull_request_webhook against the
preview Mongo to confirm merged_sha capture (merged=true) and
nothing_landed handling (merged=false). No GitHub calls (token missing →
branch_deleted=False with a branch_delete_error string).
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from services.loop_safety import dispatch_pull_request_webhook  # noqa: E402


async def run():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    branch_m = "auremcto/ship-r10test-merged"
    branch_u = "auremcto/ship-r10test-unmerged"
    merge_sha = "d" * 40

    # cleanup any prior residue
    await db.loop_outcomes.delete_many({"ship_branch": {"$in": [branch_m, branch_u]}})

    # seed merged row
    await db.loop_outcomes.insert_one({
        "loop_id": "r10test-m", "user_id": "r10test", "project_id": "r10test",
        "ship_branch": branch_m, "commit_sha": "pre-r10test-m-" + "a"*8, "pr_status": "open",
    })
    # seed unmerged row
    await db.loop_outcomes.insert_one({
        "loop_id": "r10test-u", "user_id": "r10test", "project_id": "r10test",
        "ship_branch": branch_u, "commit_sha": "pre-r10test-u-" + "b"*8, "pr_status": "open",
    })

    # -- merged variant --
    payload_m = {
        "pull_request": {
            "labels": [{"name": "aura:ship"}],
            "head": {"ref": branch_m},
            "merged": True,
            "merge_commit_sha": merge_sha,
            "number": 1,
            "html_url": "https://example/test/1",
        },
        "repository": {"full_name": "r10test/repo"},
    }
    res_m = await dispatch_pull_request_webhook(db, payload=payload_m, action="closed")
    print("merged dispatch result:", res_m)
    row_m = await db.loop_outcomes.find_one({"ship_branch": branch_m})
    assert row_m["pr_status"] == "merged", row_m
    assert row_m["merged_sha"] == merge_sha, row_m
    assert "merged_at" in row_m
    print("OK merged row:", {k: row_m.get(k) for k in ("pr_status", "merged_sha", "merged_at")})

    # -- unmerged variant --
    payload_u = {
        "pull_request": {
            "labels": [{"name": "aura:ship"}],
            "head": {"ref": branch_u},
            "merged": False,
            "merge_commit_sha": None,
            "number": 2,
            "html_url": "https://example/test/2",
        },
        "repository": {"full_name": "r10test/repo"},
    }
    res_u = await dispatch_pull_request_webhook(db, payload=payload_u, action="closed")
    print("unmerged dispatch result:", res_u)
    row_u = await db.loop_outcomes.find_one({"ship_branch": branch_u})
    assert row_u["pr_status"] == "closed", row_u
    assert row_u.get("nothing_landed") is True, row_u
    assert "merged_sha" not in row_u, row_u
    assert isinstance(row_u.get("branch_deleted"), bool), row_u
    # token missing → False + error string
    assert row_u["branch_deleted"] is False, row_u
    assert isinstance(row_u.get("branch_delete_error"), str) and row_u["branch_delete_error"], row_u
    print("OK unmerged row:", {k: row_u.get(k) for k in (
        "pr_status", "nothing_landed", "branch_deleted", "branch_delete_error")})

    # cleanup
    await db.loop_outcomes.delete_many({"ship_branch": {"$in": [branch_m, branch_u]}})
    await db.ship_pr_events.delete_many({"head_ref": {"$in": [branch_m, branch_u]}})
    print("cleanup done")


def test_r10_dispatcher_live():
    asyncio.run(run())


if __name__ == "__main__":
    asyncio.run(run())
