"""Shared CLI skeleton for backend/scripts/* Mongo tools (N5)."""
from __future__ import annotations

import os
import sys


def mongo_env():
    """Return (mongo_url, db_name), or None after printing the refusal."""
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("MONGO_URL/DB_NAME not set — refusing to run.", file=sys.stderr)
        return None
    return mongo_url, db_name


async def run_with_guarded_db(work):
    """Run await work(guarded_db) with a WriteGuardedDB.

    Returns None when MONGO_URL/DB_NAME are missing (after printing the
    refusal, exactly as the scripts did before). The client is always
    closed afterwards.
    """
    env = mongo_env()
    if env is None:
        return None
    mongo_url, db_name = env

    from motor.motor_asyncio import AsyncIOMotorClient

    from services.plan_transition_write_guard import WriteGuardedDB

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
    try:
        return await work(WriteGuardedDB(client[db_name]))
    finally:
        client.close()
