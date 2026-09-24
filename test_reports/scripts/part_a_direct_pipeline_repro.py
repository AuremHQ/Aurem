"""Independent re-verification of Part A live pipeline for both incidents #1 and #2.

Connects to the SAME live Mongo the backend server uses (via cto_services.db.set_db)
and invokes the REAL, unmodified services.chat_helpers.apply_output_guards() with
the exact real error text patterns. Then queries db.ora_self_bugs directly to
confirm new entries appear with correct source tagging.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from cto_services import db as db_module  # noqa: E402
from services.chat_helpers import apply_output_guards  # noqa: E402


LEAKED_1 = (
    "❌ FILE NOT FOUND: `injection_scanner.py` does not exist on TJSNDHU/Aurem@main. "
    "STOP guessing paths. Your next tool call MUST be `list_repo_files` with a "
    "glob (e.g. `**/auth*.py`, `**/*router*.py`) to DISCOVER the real paths in "
    "this repo. Do not write a plan, do not produce a handoff brief, do not cite "
    "any file paths — until you have called list_repo_files and seen the actual "
    "layout. Most repos do not follow the layout you expect from training data."
)

LEAKED_2 = (
    "I added the comment.\n\n"
    "```js\nconst root = ReactDOM.createRoot(x);\nroot.render(<App />);\n```\n\n"
    "```aurem-handoff\n"
    "In frontend/src/index.js add the comment at the top of the file.\n"
    "```\n"
)


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    db_module.set_db(db)

    coll = db.ora_self_bugs
    before = await coll.count_documents({"type": "tool_error"})
    print(f"[before] ora_self_bugs tool_error count = {before}")

    # Incident #1: internal_directive_guard
    session1 = f"qa-indep-part-a-1-{datetime.now(timezone.utc).isoformat()}"
    out1 = apply_output_guards(
        "check injection_scanner.py",
        LEAKED_1,
        False,
        "",
        session_id=session1,
        user_id="test_admin_indep_1",
    )
    print(f"[incident 1] STOP present in output? {'STOP guessing paths' in out1}")
    print(f"[incident 1] output len: {len(out1)}")

    # Incident #2: handoff_diff_safety_guard
    session2 = f"qa-indep-part-a-2-{datetime.now(timezone.utc).isoformat()}"
    out2 = apply_output_guards(
        "add a comment to index.js",
        LEAKED_2,
        False,
        "",
        session_id=session2,
        user_id="test_admin_indep_2",
        tool_calls_run=1,
    )
    print(f"[incident 2] stray ```js present in output? {'```js' in out2}")

    # allow fire-and-forget tasks to complete
    await asyncio.sleep(1.5)

    after = await coll.count_documents({"type": "tool_error"})
    print(f"[after] ora_self_bugs tool_error count = {after}")

    # Look up the two newest entries
    recent = await coll.find({"type": "tool_error"}).sort("_id", -1).limit(5).to_list(5)
    print(f"\n[recent tool_error entries: {len(recent)}]")
    for r in recent:
        r.pop("_id", None)
        print("  source:", r.get("source"), "| session:", r.get("context", {}).get("session_id"),
              "| severity:", r.get("severity"), "| conf:", r.get("confidence"),
              "| cause:", (r.get("likely_cause") or "")[:80])

    # Assertions
    src_by_session = {r.get("context", {}).get("session_id"): r.get("source") for r in recent}
    ok1 = src_by_session.get(session1) == "internal_directive_guard"
    ok2 = src_by_session.get(session2) == "handoff_diff_safety_guard"
    print(f"\nPASS_INCIDENT_1={ok1}  PASS_INCIDENT_2={ok2}")
    print(f"COUNT_DELTA={after - before} (expected 2)")


asyncio.run(main())
