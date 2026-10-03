"""scripts/c3_secret_cleanup_dryrun.py — DRY RUN ONLY.

Counts how many existing `cto_open_findings` and chat-history documents
still contain a raw (unmasked) secret-shaped value, per the same
detection used by `services.secret_masking.redact_secrets_in_text`.

Makes ZERO writes. Prints counts + a few redacted-preview examples
(never the raw value itself) for the founder to review before any
real cleanup is approved and run.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from motor.motor_asyncio import AsyncIOMotorClient
from services.secret_masking import redact_secrets_in_text, _QUOTED_SECRET_RE, _ENV_GETTER_DEFAULT_RE, _BARE_SECRET_SHAPE_RE


def _has_raw_secret(text: str) -> bool:
    if not text:
        return False
    return bool(
        _QUOTED_SECRET_RE.search(text)
        or _ENV_GETTER_DEFAULT_RE.search(text)
        or _BARE_SECRET_SHAPE_RE.search(text)
    )


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]

    findings_hits = 0
    findings_total = 0
    examples = []
    async for doc in db.cto_open_findings.find(
        {}, {"title": 1, "message": 1, "fix_hint": 1, "snippet": 1, "project_id": 1}
    ):
        findings_total += 1
        fields_hit = [
            f for f in ("title", "message", "fix_hint", "snippet")
            if _has_raw_secret(doc.get(f) or "")
        ]
        if fields_hit:
            findings_hits += 1
            if len(examples) < 5:
                examples.append({
                    "collection": "cto_open_findings",
                    "_id": str(doc.get("_id")),
                    "project_id": doc.get("project_id"),
                    "fields_with_raw_secret": fields_hit,
                })

    chat_hits = 0
    chat_total = 0
    async for doc in db.chat_sessions.find({}, {"messages": 1, "session_id": 1}):
        for msg in (doc.get("messages") or []):
            chat_total += 1
            if _has_raw_secret(msg.get("content") or ""):
                chat_hits += 1
                if len(examples) < 10:
                    examples.append({
                        "collection": "chat_sessions.messages[]",
                        "session_id": doc.get("session_id"),
                        "role": msg.get("role"),
                    })

    print(f"cto_open_findings: {findings_hits} / {findings_total} docs contain a raw secret-shaped value")
    print(f"chat_sessions messages: {chat_hits} / {chat_total} messages contain a raw secret-shaped value")
    print(f"TOTAL would-be-cleaned: {findings_hits + chat_hits}")
    print("\nSample hits (IDs only, no values):")
    for ex in examples:
        print(" ", ex)
    print("\nThis was a DRY RUN. Zero documents were modified.")


if __name__ == "__main__":
    asyncio.run(main())
