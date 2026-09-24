"""Backend tests for GitHub Stuck Connections admin feature (2026-01).

Endpoints:
  GET  /api/aurem-dev/admin/github/stuck-connections
  POST /api/aurem-dev/admin/github/clear-stuck-state

Verifies classification rules, safety exclusion, hygiene action,
audit doc write, and cleanup of seeded synthetic data.
"""
import os
import time
import uuid
import pytest
import requests
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"

TEST_PREFIX = "test_stuck_2026_01_"

# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL)
    db = c[DB_NAME]
    yield db
    # cleanup
    db.github_funnel_events.delete_many({"user_id": {"$regex": f"^{TEST_PREFIX}"}})
    db.cto_projects.delete_many({"user_id": {"$regex": f"^{TEST_PREFIX}"}})
    db.dev_users.delete_many({"user_id": {"$regex": f"^{TEST_PREFIX}"}})
    db.oauth_states.delete_many({"user_id": {"$regex": f"^{TEST_PREFIX}"}})
    db.admin_audit.delete_many({"user_ids": {"$elemMatch": {"$regex": f"^{TEST_PREFIX}"}}})
    c.close()


@pytest.fixture(scope="module")
def admin_token():
    # Try common login endpoints
    for path in ["/api/aurem-dev/auth/login", "/api/auth/login"]:
        try:
            r = requests.post(f"{BASE_URL}{path}",
                              json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                              timeout=20)
            if r.status_code == 200:
                data = r.json()
                tok = data.get("token") or data.get("access_token") or data.get("jwt")
                if tok:
                    return tok
        except Exception:
            continue
    pytest.skip("Admin login failed - cannot obtain token")


@pytest.fixture(scope="module")
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def seeded_users(mongo):
    """Seed 4 users covering all classification cases."""
    now = datetime.now(timezone.utc)
    uid_a = TEST_PREFIX + "userA_" + uuid.uuid4().hex[:6]
    uid_b = TEST_PREFIX + "userB_" + uuid.uuid4().hex[:6]
    uid_c = TEST_PREFIX + "userC_" + uuid.uuid4().hex[:6]
    uid_d = TEST_PREFIX + "userD_" + uuid.uuid4().hex[:6]

    # dev_users rows for email lookup
    mongo.dev_users.insert_many([
        {"user_id": uid_a, "email": f"{uid_a}@example.com"},
        {"user_id": uid_b, "email": f"{uid_b}@example.com"},
        {"user_id": uid_c, "email": f"{uid_c}@example.com"},
        {"user_id": uid_d, "email": f"{uid_d}@example.com"},
    ])

    # A: only app_install_redirect -> MUST appear as stuck
    mongo.github_funnel_events.insert_one({
        "user_id": uid_a, "stage": "app_install_redirect",
        "source": "test", "session_id": "s_a", "meta": {},
        "created_at": now - timedelta(minutes=10),
    })
    # Also seed a clearable oauth_states row for A (checkbox enabled case)
    mongo.oauth_states.insert_one({
        "user_id": uid_a, "kind": "github_app_install", "used": False,
        "state": "tok_a_" + uuid.uuid4().hex[:8],
        "created_at": now - timedelta(minutes=10),
    })

    # B: denied then granted later -> MUST NOT appear
    mongo.github_funnel_events.insert_many([
        {"user_id": uid_b, "stage": "app_install_denied",
         "source": "test", "session_id": "s_b1", "meta": {},
         "created_at": now - timedelta(hours=2)},
        {"user_id": uid_b, "stage": "app_install_granted",
         "source": "test", "session_id": "s_b2", "meta": {},
         "created_at": now - timedelta(minutes=5)},
    ])

    # C: only connect_repo_click -> MUST NOT appear (out of scope)
    mongo.github_funnel_events.insert_one({
        "user_id": uid_c, "stage": "connect_repo_click",
        "source": "test", "session_id": "s_c", "meta": {},
        "created_at": now - timedelta(minutes=10),
    })

    # D: latest=denied BUT has working cto_projects github_app row
    # -> MUST NOT appear (safety exclusion)
    mongo.github_funnel_events.insert_one({
        "user_id": uid_d, "stage": "app_install_denied",
        "source": "test", "session_id": "s_d", "meta": {"reason": "declined"},
        "created_at": now - timedelta(minutes=10),
    })
    mongo.cto_projects.insert_one({
        "user_id": uid_d, "auth_method": "github_app",
        "installation_id": 99999999,
        "project_id": TEST_PREFIX + "proj_d",
        "created_at": now,
    })

    return {"A": uid_a, "B": uid_b, "C": uid_c, "D": uid_d}


# ---------------------------------------------------------------- tests

class TestStuckConnections:

    def test_endpoint_reachable(self, headers):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/github/stuck-connections",
                         headers=headers, timeout=30)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert data.get("ok") is True
        assert isinstance(data.get("rows"), list)

    def test_unauth_rejected(self):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/github/stuck-connections",
                         timeout=20)
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"

    def test_classification_rules(self, seeded_users, headers):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/github/stuck-connections",
                         headers=headers, timeout=30)
        assert r.status_code == 200
        rows = r.json()["rows"]
        uids = {row["user_id"] for row in rows}

        # A: latest=redirect -> present
        assert seeded_users["A"] in uids, "User A (latest=redirect) should appear"
        row_a = next(r for r in rows if r["user_id"] == seeded_users["A"])
        assert row_a["stage"] == "app_install_redirect"
        assert row_a["pending_oauth_states"] >= 1, "Should count seeded oauth_states"
        assert row_a["email"] == f"{seeded_users['A']}@example.com"

        # B: latest=granted (after denial) -> absent
        assert seeded_users["B"] not in uids, \
            "User B (later granted) must NOT appear — latest event wins"

        # C: latest=connect_repo_click -> absent
        assert seeded_users["C"] not in uids, \
            "User C (connect_repo_click) must NOT appear — out of scope"

        # D: latest=denied but has working github_app project -> absent
        assert seeded_users["D"] not in uids, \
            "User D must NOT appear — safety exclusion (has working install)"

    def test_clear_stuck_hygiene_and_audit(self, seeded_users, headers, mongo):
        uid_a = seeded_users["A"]
        # Verify oauth_states row exists before
        before = mongo.oauth_states.count_documents(
            {"user_id": uid_a, "kind": "github_app_install", "used": False})
        assert before >= 1

        r = requests.post(
            f"{BASE_URL}/api/aurem-dev/admin/github/clear-stuck-state",
            headers=headers,
            json={"user_ids": [uid_a], "reason": "test cleanup 2026-01"},
            timeout=30,
        )
        assert r.status_code == 200, f"Got {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert data["ok"] is True
        assert data["cleared_rows"] >= 1
        assert data["users_processed"] == 1
        # hygiene-only framing in response
        assert "no effect" in data["note"].lower(), \
            f"Response note must contain 'no effect', got: {data['note']}"

        # Verify DB deletion
        after = mongo.oauth_states.count_documents(
            {"user_id": uid_a, "kind": "github_app_install", "used": False})
        assert after == 0, "oauth_states rows should be deleted"

        # Verify audit doc
        audit = mongo.admin_audit.find_one({
            "action": "clear_github_stuck_state",
            "user_ids": uid_a,
        })
        assert audit is not None, "admin_audit doc must be inserted"
        assert audit.get("reason") == "test cleanup 2026-01"
        assert audit.get("cleared_rows") >= 1

    def test_clear_empty_user_ids_rejected(self, headers):
        r = requests.post(
            f"{BASE_URL}/api/aurem-dev/admin/github/clear-stuck-state",
            headers=headers, json={"user_ids": [], "reason": "x"}, timeout=15,
        )
        assert r.status_code in (400, 422), f"Empty user_ids should 400/422, got {r.status_code}"
