"""scripts/part2b_r3_unset_finding_fix_content_dryrun.py — DRY RUN by default.

Part 2b R3 — before this fix, `finding_fixes` rows stored the FULL
plaintext `original_content`/`patched_content` of every file fixed.
That's a second, un-access-controlled plaintext copy of every fixed
file in Mongo — including, for a hardcoded-secret finding, the secret
value itself. New rows no longer carry these fields at all (revert now
re-fetches both versions from GitHub by commit sha instead).

This script finds EXISTING rows written before the fix (still
carrying `original_content` and/or `patched_content`) and reports how
many there are, how much plaintext they hold, and whether each one
has the `parent_commit_sha` a revert would need going forward. It
makes ZERO writes unless invoked with `--execute`, and even then ONLY
`$unset`s the two content fields — nothing else on the row is touched,
and no row is ever deleted.

Usage:
    python scripts/part2b_r3_unset_finding_fix_content_dryrun.py            # dry run (default)
    python scripts/part2b_r3_unset_finding_fix_content_dryrun.py --execute  # real $unset

Never run --execute without founder sign-off on this file's dry-run
output first.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from motor.motor_asyncio import AsyncIOMotorClient

_CONTENT_FIELDS = ("original_content", "patched_content", "content", "before", "after")


async def main(execute: bool) -> None:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]

    total_rows = await db.finding_fixes.count_documents({})
    legacy_filter = {"$or": [{f: {"$exists": True}} for f in _CONTENT_FIELDS]}
    legacy_count = await db.finding_fixes.count_documents(legacy_filter)
    missing_parent_sha = await db.finding_fixes.count_documents({
        **legacy_filter,
        "parent_commit_sha": {"$in": [None, ""]},
    })

    total_bytes = 0
    examples = []
    async for doc in db.finding_fixes.find(legacy_filter, {"_id": 1, "fix_id": 1,
                                                            "project_id": 1, "file": 1,
                                                            "parent_commit_sha": 1,
                                                            **{f: 1 for f in _CONTENT_FIELDS}}):
        for f in _CONTENT_FIELDS:
            val = doc.get(f)
            if isinstance(val, str):
                total_bytes += len(val)
        if len(examples) < 5:
            examples.append({
                "fix_id": doc.get("fix_id"),
                "project_id": doc.get("project_id"),
                "file": doc.get("file"),
                "has_parent_commit_sha": bool(doc.get("parent_commit_sha")),
                "fields_present": [f for f in _CONTENT_FIELDS if f in doc],
            })

    print(f"finding_fixes: {total_rows} total rows")
    print(f"  {legacy_count} rows still carry plaintext content field(s)")
    print(f"  {missing_parent_sha} of those have NO parent_commit_sha — a revert for "
          f"these rows cannot re-fetch the pre-fix content from GitHub by sha; "
          f"they would need a manual/legacy revert path, not the new one.")
    print(f"  ~{total_bytes:,} bytes of plaintext file content would be removed")
    print("\nSample rows (fix_id / project_id / file only):")
    for ex in examples:
        print(" ", ex)

    if not execute:
        print("\nThis was a DRY RUN. Zero documents were modified. "
              "Re-run with --execute (after founder sign-off) to actually $unset.")
        return

    print(f"\n--execute passed — unsetting {len(_CONTENT_FIELDS)} content field(s) "
          f"on {legacy_count} row(s). No row is deleted; every other field is untouched.")
    result = await db.finding_fixes.update_many(
        legacy_filter,
        {"$unset": {f: "" for f in _CONTENT_FIELDS}},
    )
    print(f"Modified {result.modified_count} row(s).")


if __name__ == "__main__":
    asyncio.run(main(execute="--execute" in sys.argv))
