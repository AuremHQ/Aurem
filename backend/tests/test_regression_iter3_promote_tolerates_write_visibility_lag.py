"""
test_regression_iter3_promote_tolerates_write_visibility_lag.py

Real bug found while diagnosing quality-gate.yml's "Fitness-function
invariants" job (2026-09-29): test_loop_gate_chat_stream_2026_01.py /
test_loop_gate_killswitch_2026_01b.py / test_loop_gate_parity_and_mode_d_2026_01.py
each define a `_promote(mongo, email, tier)` helper that signs a user
up via a live /auth/signup HTTP call, then immediately `update_one`s
that same email over a SEPARATE pymongo connection. Under the full
suite's concurrent Mongo load (~700 tests running in ~5 minutes on
this pod's single mongod), that update sometimes ran within
single-digit milliseconds of /auth/signup returning 200 and raced
ahead of that insert becoming visible on the separate connection —
confirmed live: `matched_count=0` immediately after signup, then the
identical row present (with a near-identical `created_at`) a fraction
of a second later. Never reproduces in isolation / light load — only
under the full suite's contention.

Fix: `_promote` now retries `update_one` for up to ~2s (20 x 100ms)
before failing, in all three files. This test proves the retry loop
tolerates a delayed write instead of failing on the first miss,
without ever weakening the actual assertion (a row that truly never
appears still fails loud).
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import threading
import time
import uuid

import pytest
from pymongo import MongoClient

_TESTS_DIR = pathlib.Path(__file__).resolve().parent


def _load_module(filename: str):
    """Import a non-package test file by path (tests/ has no __init__.py)."""
    path = _TESTS_DIR / filename
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _db():
    client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=3000)
    database = client[os.environ.get("DB_NAME", "aurem_dev")]
    yield database
    client.close()


def test_regression_iter3_promote_tolerates_write_visibility_lag(_db):
    """A row that appears 300ms AFTER _promote's first attempt (simulating
    the under-load write-visibility lag) must still be promoted
    successfully — the retry loop must not give up on the first miss."""
    mod = _load_module("test_loop_gate_chat_stream_2026_01.py")
    email = f"promote-lag-{uuid.uuid4().hex[:8]}@aurem-test.dev"

    def _delayed_insert():
        time.sleep(0.3)
        _db.dev_users.insert_one({"email": email, "signup_ip": "127.0.0.1",
                                   "created_at": time.time(), "tier": "free"})

    threading.Thread(target=_delayed_insert, daemon=True).start()

    mod._promote(_db, email, "pro")  # must NOT raise despite the delay

    row = _db.dev_users.find_one({"email": email})
    assert row is not None and row["tier"] == "pro"
    _db.dev_users.delete_one({"email": email})


def test_regression_iter3_promote_still_fails_for_a_row_that_never_appears(_db):
    """Sanity check: the retry loop is bounded — a genuinely missing
    row (no delayed insert at all) must still raise, not hang or
    silently pass. Keeps this a real fix, not a loosened assertion."""
    mod = _load_module("test_loop_gate_chat_stream_2026_01.py")
    email = f"promote-never-{uuid.uuid4().hex[:8]}@aurem-test.dev"

    with pytest.raises(AssertionError, match="no dev_users row"):
        mod._promote(_db, email, "pro")
