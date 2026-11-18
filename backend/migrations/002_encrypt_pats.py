"""Migration 002 — PAT encryption (REMOVED 2026-11-17).

This migration used to encrypt plaintext GitHub PATs in
``cto_projects.github_token`` with the former vault module (HKDF-Fernet).
Crypto was deleted from the codebase by decision, so there is nothing
left to encrypt: PATs are stored in plaintext and this migration is
now a no-op that only records itself as applied (so the migration
history stays consistent for deployments that already ran it).

Legacy invocation (still works): python -m migrations.002_encrypt_pats
"""
from __future__ import annotations

import asyncio
import os

from motor.motor_asyncio import AsyncIOMotorClient

from .base import Migration


class EncryptPatsMigration(Migration):
    version = "002"
    name = "encrypt_pats"
    description = "No-op since crypto removal (2026-11-17): PATs are stored in plaintext; nothing to encrypt."
    dev_only = False
    irreversible = True  # nothing to roll back — this migration is a no-op

    async def up(self, db) -> None:
        # No-op: encryption was removed from the codebase. PATs remain
        # stored in plaintext by decision; there is nothing to migrate.
        import logging
        logging.getLogger("aurem.migrations").info(
            "002_encrypt_pats: no-op (crypto removed); migrated=0 already=0 skipped=0"
        )

    async def down(self, db) -> None:
        raise NotImplementedError(
            "002_encrypt_pats is a no-op since crypto removal: nothing to roll back."
        )


# ---------------------------------------------------------------------------
# Legacy CLI shim
# ---------------------------------------------------------------------------
async def migrate():
    mongo_uri = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=10_000)
    try:
        db = client[db_name]
        print("Running 002_encrypt_pats (legacy shim)...")
        await EncryptPatsMigration().up(db)
        print("✓ 002_encrypt_pats complete.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(migrate())
