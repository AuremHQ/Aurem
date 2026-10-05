"""Admin Team S1 (AT1a) — "no behaviour change, records what the new
rules WOULD refuse" regression lock.

Real Preview Mongo (same pattern as test_regression_iter_adminteam_s0_
allowlist.py), real JWTs via cto_services.auth.create_token, the REAL
require_admin_dep (no dependency_overrides) — this must exercise the
actual gate, not a stub, since the whole point is proving the recording
side-effect fires (or doesn't) for real.
"""
import os
import time
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from server import app
from cto_services.auth import create_token

_LEGACY_USER_ID = "regtest-fix3-s1-legacy-founder-user"
_OWNER_EMAIL = "test@aurem.dev"  # on FOUNDER_EMAILS per backend/.env — real owner


@pytest.fixture
def mongo():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        pytest.skip("MONGO_URL/DB_NAME not configured in this environment")
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
    db = client[db_name]
    yield db
    client.close()


@pytest.fixture
def legacy_founder_row(mongo):
    """A dev_users row with tier=="founder" but an email that is NOT on
    the founder allow-list — exactly the drift D1's lock test targets."""
    doc = {
        "user_id": _LEGACY_USER_ID,
        "email": "legacy-founder-not-on-allowlist@regtest.local",
        "tier": "founder",
        "is_admin": False,
        "created_at": time.time(),
    }
    mongo.dev_users.delete_one({"user_id": _LEGACY_USER_ID})
    mongo.dev_users.insert_one(doc)
    yield doc
    mongo.dev_users.delete_one({"user_id": _LEGACY_USER_ID})


def _would_refuse_count_for(mongo, user_id: str) -> int:
    return mongo.admin_gate_would_refuse_log.count_documents({"user_id": user_id})


def test_regression_iter_adminteam_s1_legacy_founder_still_gets_200_but_is_recorded(
    mongo, legacy_founder_row
):
    mongo.admin_gate_would_refuse_log.delete_many({"user_id": _LEGACY_USER_ID})
    token = create_token(_LEGACY_USER_ID, legacy_founder_row["email"], is_admin=False)
    with TestClient(app) as client:
        resp = client.get(
            "/api/aurem-dev/admin/users",
            params={"search": "nonexistent-marker-regtest"},
            headers={"Authorization": f"Bearer {token}"},
        )
    # No behaviour change yet — AT1a must not refuse anything new.
    assert resp.status_code == 200, (
        "S1 must not change behaviour: a legacy tier==founder row still "
        f"gets 200 today, got {resp.status_code}: {resp.text[:300]}"
    )
    count = _would_refuse_count_for(mongo, _LEGACY_USER_ID)
    assert count >= 1, (
        "D1 would refuse this account (tier==founder, not on the allow-list) "
        "under the new rules — the gate must record that, even though it "
        "does not act on it yet"
    )
    row = mongo.admin_gate_would_refuse_log.find_one({"user_id": _LEGACY_USER_ID})
    assert row["path"] == "/api/aurem-dev/admin/users"
    assert "allow-list" in row["reason"]
    mongo.admin_gate_would_refuse_log.delete_many({"user_id": _LEGACY_USER_ID})


def test_regression_iter_adminteam_s1_owner_session_has_empty_would_refuse_record(mongo):
    """GATE proof for S1: 'would-refuse record is empty for an owner
    session on Preview.' Uses the real owner account (test@aurem.dev,
    on FOUNDER_EMAILS) — requires TEST_OWNER_PASSWORD in the environment
    (see /app/memory/test_credentials.md); skips if not provided rather
    than hardcoding a credential in test source."""
    password = os.environ.get("ADMIN_TEAM_TEST_OWNER_PASSWORD")
    if not password:
        pytest.skip("ADMIN_TEAM_TEST_OWNER_PASSWORD not set — see STAGE-1.md for the real-Preview curl proof instead")
    row = mongo.dev_users.find_one({"email": _OWNER_EMAIL}, {"user_id": 1})
    assert row, f"{_OWNER_EMAIL} must exist on this Preview for this test"
    mongo.admin_gate_would_refuse_log.delete_many({"user_id": row["user_id"]})
    token = create_token(row["user_id"], _OWNER_EMAIL, is_admin=True)
    with TestClient(app) as client:
        resp = client.get(
            "/api/aurem-dev/admin/users",
            params={"search": "nonexistent-marker-regtest"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert _would_refuse_count_for(mongo, row["user_id"]) == 0
