"""One-shot seeder for the Bug 2 UI test. Prints the session_id."""
import os, sys, time, uuid
sys.path.insert(0, "/app/backend")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aurem_dev")

from pymongo import MongoClient  # noqa

action = sys.argv[1] if len(sys.argv) > 1 else "seed"
mc = MongoClient(os.environ["MONGO_URL"])
db = mc[os.environ["DB_NAME"]]

if action == "seed":
    session_id = f"TEST_bug2_ui_{uuid.uuid4().hex[:8]}"
    handoff = (
        "Here's the plan:\n\n"
        "```aurem-handoff\n"
        "Fix the footer copyright text in src/App.js to say "
        "'regression test rollback check' - preserve every existing import.\n"
        "```\n"
    )
    db.chat_sessions.insert_one({
        "session_id": session_id,
        "user_id": "test_admin_001",
        "project_id": "p_demo_a",
        "created_at": time.time(),
        "updated_at": time.time(),
        "turns": [
            {"role": "user", "content": "update the footer copyright", "ts": time.time()-2},
            {"role": "assistant", "content": handoff, "provider": "test-seed", "ts": time.time()-1},
        ],
    })
    print(session_id)
elif action == "check":
    sid = sys.argv[2]
    doc = db.chat_sessions.find_one({"session_id": sid}, {"_id": 0, "turns": 1})
    if not doc:
        print("MISSING")
    else:
        turns = doc.get("turns", [])
        flags = [t.get("ship_cancelled", False) for t in turns]
        print(f"turns={len(turns)} ship_cancelled_flags={flags}")
elif action == "delete":
    sid = sys.argv[2]
    r = db.chat_sessions.delete_one({"session_id": sid})
    print(f"deleted={r.deleted_count}")
