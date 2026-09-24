"""Read-only: which App installation covers aurem-rollback-testbed?"""
import asyncio
import json
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

TARGET = "aurem-rollback-testbed"


async def main():
    from services.github_app import list_installations, get_installation_token
    from services.http import ext_client
    import httpx

    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.github_app_config import ensure_configured_from_db
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print("app configured:", await ensure_configured_from_db(db))
    installs = await list_installations()
    print(f"total installations: {len(installs)}")
    hits = []
    for inst in installs:
        iid = inst["id"]
        acct = (inst.get("account") or {}).get("login")
        try:
            token, _ = await get_installation_token(iid)
        except Exception as e:  # noqa: BLE001
            print(f"  {iid} {acct}: token error {e!r}")
            continue
        async with ext_client("github", timeout=httpx.Timeout(15.0)) as c:
            r = await c.get(
                "https://api.github.com/installation/repositories?per_page=100",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
            )
        repos = [x["full_name"] for x in (r.json().get("repositories") or [])]
        print(f"  {iid} {acct} sel={inst.get('repository_selection')} repos={repos}")
        if any(n.split("/")[-1] == TARGET for n in repos):
            hits.append({"installation_id": iid, "account": acct, "repos": repos})
    print(json.dumps({"target": TARGET, "hits": hits}, indent=1))


asyncio.run(main())
