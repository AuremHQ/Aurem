# Test-only helper: toggle harness_page feature flag (on|off|show). "off" deletes the doc (original state = no doc).
import os, sys, asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def main(cmd):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    if cmd == "on":
        await db.feature_flags.update_one({"flag": "harness_page"}, {"$set": {"enabled": True}}, upsert=True)
    elif cmd == "off":
        r = await db.feature_flags.delete_many({"flag": "harness_page"})
        print("deleted", r.deleted_count)
    print(await db.feature_flags.find({"flag": "harness_page"}, {"_id": 0}).to_list(10))


asyncio.run(main(sys.argv[1]))
