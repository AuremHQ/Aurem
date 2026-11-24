"""
cto_services/db.py — AUREM Dev
Database handle — set at startup, read by routers.

2026-11-24 — guard-crash fix. admin_health.py's `/admin/status/all`
runs every guard/health check off the request event loop (its own
persistent background loop/thread) so a blocking check_fn can never
freeze `/health` (real 2026-11-21 prod incident). Motor's
AsyncIOMotorClient binds internal asyncio.Lock/Future objects to
whichever loop is running the first time it's used — reusing the
SAME client instance from a DIFFERENT loop crashes with
"Task ... got Future ... attached to a different loop", which is
exactly why G1/G5/G10/G15/G20/infra_db were showing red in the
cockpit. get_db() is now loop-aware: on the main app loop it returns
the original startup client, unchanged; on any other loop it lazily
creates (and caches, once per loop — not once per call) a small
dedicated client for that loop.
"""
import asyncio
import os

from fastapi import HTTPException

_db = None
_main_loop = None       # the loop running when set_db() was called
_loop_dbs: dict = {}     # id(loop) -> db handle, for any OTHER loop


def set_db(database) -> None:
    global _db, _main_loop
    _db = database
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        _main_loop = None


def _secondary_db_for_current_loop():
    """Lazily create a small dedicated Motor client for a loop that
    is NOT the main app loop, caching it by loop id. admin_health.py
    uses exactly one persistent background loop for all checks, so
    this creates one extra client for the life of the process — not
    one per check or per request."""
    loop = asyncio.get_running_loop()
    key = id(loop)
    cached = _loop_dbs.get(key)
    if cached is not None:
        return cached
    mongo_url = os.environ.get("MONGO_URL", "")
    db_name = os.environ.get("DB_NAME", "aurem_dev")
    if not mongo_url:
        return None
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(
        mongo_url,
        maxPoolSize=5,
        minPoolSize=0,
        maxIdleTimeMS=120_000,
        serverSelectionTimeoutMS=5_000,
        connectTimeoutMS=10_000,
        retryWrites=True,
    )
    db = client[db_name]
    _loop_dbs[key] = db
    return db


def get_db():
    if _db is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _db
    if _main_loop is None or loop is _main_loop:
        return _db
    return _secondary_db_for_current_loop()


def require_db():
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    return db
