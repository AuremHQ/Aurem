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

2026-11-25 — Part VII fix 1(d): the cache used to be keyed by
id(loop). An id() is recycled once its loop is GC'd, so a NEW loop
could inherit a STALE client bound to a dead loop — the exact crash
this code was written to avoid — and nothing ever closed or evicted
the per-loop clients. The cache is now keyed by the loop OBJECT
itself (WeakKeyDictionary: no id reuse, entries drop when the loop
is GC'd) and each client's life is tied to its loop: closed loops
are swept (client closed + entry evicted) on the next lookup, and
a weakref.finalize closes the client when its loop is GC'd.
"""
import asyncio
import os
import weakref

from fastapi import HTTPException

_db = None
_main_loop = None       # the loop running when set_db() was called
# loop object -> db handle, for any OTHER loop. Weak keys: the dict
# never keeps a dead loop (or its client) alive, and a new loop can
# never collide with a recycled id() the way the old id(loop) key did.
_loop_dbs: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, object]" = (
    weakref.WeakKeyDictionary()
)


def set_db(database) -> None:
    global _db, _main_loop
    _db = database
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        _main_loop = None


def _close_loop_client(db) -> None:
    """Close the Motor client behind a secondary db handle. Called when
    its loop closes (sweep below) or is GC'd (weakref.finalize). Never
    raises — cleanup must not fail a request. `db.client` is Motor's
    public accessor for the owning client."""
    try:
        client = db.client
    except Exception:
        return
    try:
        client.close()
    except Exception:
        pass


def _secondary_db_for_current_loop():
    """Lazily create a small dedicated Motor client for a loop that
    is NOT the main app loop, caching it by loop OBJECT. admin_health.py
    uses exactly one persistent background loop for all checks, so
    this creates one extra client for the life of the process — not
    one per check or per request."""
    loop = asyncio.get_running_loop()
    # Sweep: close + evict clients whose loop has closed since we last
    # looked, so a dead loop's pool never lingers.
    for old_loop, old_db in list(_loop_dbs.items()):
        if old_loop.is_closed():
            _close_loop_client(old_db)
            try:
                del _loop_dbs[old_loop]
            except KeyError:
                pass
    cached = _loop_dbs.get(loop)
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
    _loop_dbs[loop] = db
    # When this loop is eventually GC'd, close its client: without this
    # the WeakKeyDictionary entry would drop but the client's pool would
    # linger un-closed. finalize holds a strong ref to `db` (not to the
    # loop), so the client stays alive exactly as long as the loop does.
    weakref.finalize(loop, _close_loop_client, db)
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
