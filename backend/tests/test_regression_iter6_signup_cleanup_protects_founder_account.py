"""
test_regression_iter6_signup_cleanup_protects_founder_account.py

Second real bug found while diagnosing quality-gate.yml's "Fitness-
function invariants" job (2026-09-29), on top of iter2's grace-period
fix: the CI-seeded founder account (FOUNDER_EMAILS, seeded ONCE at
backend boot via a curl signup from signup_ip="127.0.0.1") is a
SESSION-LONG fixture meant to survive the whole 3+ minute run — but
its signup_ip also matches `_TEST_IPS`, so ~60s into the run the
per-test `clean_signup_rate_limit` autouse fixture's grace-period
sweep (iter2) deleted it anyway (60s < 3 minutes). Every founder
login attempt after that point got a generic 401 "Invalid
credentials" (the row was simply gone), cascading into dozens of
unrelated FAILED/ERROR results across unrelated test files.

Confirmed live: a full invariants run showed the founder's
`dev_users` row missing entirely partway through, despite being
seeded and used successfully at the very start of the run.

Fix: `_clear_signup_state_for_test_ips()` now also excludes any email
in FOUNDER_EMAILS / ADMIN_EMAIL from the sweep, by email, regardless
of age — the IP-based per-test sweep (and its grace period) stays
exactly as-is for every other test-IP signup.
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


def test_regression_iter6_founder_row_survives_the_sweep_however_old(
    _users_collection, monkeypatch
):
    """A dev_users row whose email is in FOUNDER_EMAILS must never be
    deleted by the per-test signup-cleanup sweep, no matter how old
    it is — it's a session-long seeded fixture, not a per-test
    signup artifact."""
    users = _users_collection
    founder_email = f"founder-{uuid.uuid4().hex[:8]}@aurem.dev"
    monkeypatch.setenv("FOUNDER_EMAILS", founder_email)
    users.insert_one({
        "email": founder_email,
        "signup_ip": _TEST_IPS[0],  # "127.0.0.1" — same as the real CI seed step
        "created_at": time.time() - 600,  # well past any grace period
    })

    # Both the per-test (grace_period_s=60) and sessionstart
    # (grace_period_s=0) call sites must protect the founder row.
    _clear_signup_state_for_test_ips(grace_period_s=60)
    assert users.find_one({"email": founder_email}) is not None, (
        "founder account was swept by the per-test cleanup — this is "
        "the exact bug that turned every founder login after ~60s "
        "into a cascading 401 'Invalid credentials'"
    )

    _clear_signup_state_for_test_ips()  # sessionstart's default call
    assert users.find_one({"email": founder_email}) is not None, (
        "founder account was swept by the sessionstart cleanup"
    )


def test_regression_iter6_non_founder_test_ip_rows_still_swept(
    _users_collection, monkeypatch
):
    """Protecting the founder must not weaken the original 429-cascade
    fix: an ordinary test-IP signup with a DIFFERENT email must still
    be swept once past the grace period."""
    users = _users_collection
    monkeypatch.setenv("FOUNDER_EMAILS", "test@aurem.dev")
    other_email = f"ordinary-{uuid.uuid4().hex[:8]}@aurem-test.dev"
    users.insert_one({
        "email": other_email,
        "signup_ip": _TEST_IPS[0],
        "created_at": time.time() - 120,
    })

    _clear_signup_state_for_test_ips(grace_period_s=60)

    assert users.find_one({"email": other_email}) is None, (
        "a non-founder test-IP signup older than the grace period "
        "should still be swept"
    )
