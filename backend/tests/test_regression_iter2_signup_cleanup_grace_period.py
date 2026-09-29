"""
test_regression_iter2_signup_cleanup_grace_period.py

Real bug found while diagnosing quality-gate.yml's "Fitness-function
invariants" job (2026-09-29): tests/conftest.py's per-test autouse
`clean_signup_rate_limit` fixture called
`_clear_signup_state_for_test_ips()` unconditionally before EVERY
test, deleting ALL dev_users rows with a test-IP `signup_ip` —
including rows a MODULE/session-scoped fixture in a DIFFERENT test
file had just created moments earlier for a live signup meant to
persist across that whole file (e.g.
test_loop_gate_chat_stream_2026_01.py's `pro_token`/`team_token`).

Confirmed live in a full invariants run: those fixtures' `_promote()`
step failed with "no dev_users row for <email>", and later
`/chat/stream` calls with an otherwise-valid JWT got a 404 "User not
found", because the row was deleted between fixture setup and use —
cascading into dozens of unrelated FAILED/ERROR results.

Fix: `_clear_signup_state_for_test_ips()` now accepts a
`grace_period_s` and skips any dev_users row younger than that age.
The per-test autouse fixture passes 60s (long enough for one test's
setup, short enough to still sweep stale rows from earlier, finished
tests/files — keeping the original 429-cascade fix intact); the
one-time `pytest_sessionstart` call keeps grace_period_s=0 since
nothing from this session exists yet.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from pymongo import MongoClient

from tests.conftest import _clear_signup_state_for_test_ips, _TEST_IPS


@pytest.fixture
def _users_collection():
    client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=3000)
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    yield db.dev_users
    client.close()


def _insert_test_row(users, age_s: float) -> str:
    email = f"conftest-cleanup-{uuid.uuid4().hex[:8]}@aurem-test.dev"
    users.insert_one({
        "email": email,
        "signup_ip": _TEST_IPS[0],  # "127.0.0.1"
        "created_at": time.time() - age_s,
    })
    return email


def test_regression_iter2_signup_cleanup_grace_period(_users_collection):
    """A row created seconds ago (an ACTIVE fixture's live user) must
    survive the per-test sweep; a row from a finished, earlier test
    (older than the grace period) must still be swept."""
    users = _users_collection
    fresh_email = _insert_test_row(users, age_s=1)
    stale_email = _insert_test_row(users, age_s=120)

    _clear_signup_state_for_test_ips(grace_period_s=60)

    assert users.find_one({"email": fresh_email}) is not None, (
        "a signup created 1s ago (in-use by an active fixture) was "
        "wiped by the per-test cleanup — this is the exact bug that "
        "broke test_loop_gate_chat_stream_2026_01.py"
    )
    assert users.find_one({"email": stale_email}) is None, (
        "a signup from 120s ago (a finished, earlier test) should "
        "still be swept so the 24h/3-signup rate limit doesn't "
        "cascade across the session"
    )


def test_regression_iter2_sessionstart_cleanup_still_unconditional(_users_collection):
    """The one-time pytest_sessionstart call (grace_period_s=0, its
    default) must still wipe every matching row regardless of age —
    only the PER-TEST autouse fixture gained the grace period."""
    users = _users_collection
    email = _insert_test_row(users, age_s=1)

    _clear_signup_state_for_test_ips()  # default grace_period_s=0

    assert users.find_one({"email": email}) is None
