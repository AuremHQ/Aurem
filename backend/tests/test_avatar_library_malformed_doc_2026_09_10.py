"""
Regression test for the "Avatar library upload not working" bug —
2026-09-10.

Root cause: a malformed/pre-migration `video_pipeline_avatars` doc
missing the `avatar_id` field caused `GET /admin/video/avatars` to
raise an unhandled `KeyError`, 500ing the whole endpoint. Since the
frontend's `refreshAll()` used a single `Promise.all([...])`, that one
failure silently blanked the entire Video panel (keys card, avatar
library, AND job history all failed to load) with zero visible error.

Fix: `list_avatars` now filters malformed docs out at the query level
(`avatar_id: {"$exists": True}`) and defensively skips any doc missing
`avatar_id`/`r2_key`/`kind` in the loop instead of raising. The
frontend also now fetches secrets/avatars/jobs independently (not via
a single all-or-nothing Promise.all) and surfaces a visible error in
the avatar library section if it fails.
"""
import os
import time
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/aurem-dev/auth/login",
                       json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"Login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("token") or r.json().get("access_token")
    if not tok:
        pytest.skip("No token in login response")
    return tok


@pytest.fixture
def headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_avatars_survives_malformed_doc(headers):
    """Inject a doc missing avatar_id (the exact prod bug), confirm
    GET /admin/video/avatars still returns 200 instead of 500."""
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL/DB_NAME not available in this test process")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    bad_doc = {
        "admin_id": "test_admin_001", "kind": "image",
        "label": "regression_malformed_doc", "uploaded_at": time.time(),
    }
    result = await db.video_pipeline_avatars.insert_one(bad_doc)
    try:
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/video/avatars",
                          headers=headers, timeout=15)
        assert r.status_code == 200, f"list_avatars must not 500 on a malformed doc, got {r.status_code}: {r.text[:300]}"
        avatars = r.json().get("avatars", [])
        # the malformed doc must be silently skipped, never surfaced
        assert all(a.get("avatar_id") for a in avatars)
        assert not any(a.get("label") == "regression_malformed_doc" for a in avatars)
    finally:
        await db.video_pipeline_avatars.delete_one({"_id": result.inserted_id})
