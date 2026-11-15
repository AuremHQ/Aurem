# Live (public URL) regression tests for /harness Fix 2 (waitlist) + Fix 3 (public GETs)
import os
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
URL = f"{BASE_URL}/api/harness/waitlist"


def _ip():
    # unique fake client IP per test so the 5/min per-IP bucket doesn't bleed across tests
    return f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"


def _email():
    return f"test-waitlist-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(scope="module")
def mongo():
    from dotenv import dotenv_values
    env = dotenv_values("/app/backend/.env")
    client = MongoClient(env["MONGO_URL"])
    db = client[env["DB_NAME"]]
    created = []
    yield db, created
    for e in created:
        db.harness_waitlist.delete_one({"email": e})
    client.close()


# Waitlist: valid email -> 200 + persisted with correct fields
def test_valid_email_persists(mongo):
    db, created = mongo
    e = _email()
    created.append(e)
    r = requests.post(URL, json={"email": e}, headers={"X-Forwarded-For": _ip()}, timeout=20)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    doc = db.harness_waitlist.find_one({"email": e})
    assert doc is not None
    assert doc["source"] == "harness_page"
    assert isinstance(doc["joined_at"], (int, float))


# Waitlist: duplicate -> still 200, single row, joined_at preserved
def test_duplicate_email_idempotent(mongo):
    db, created = mongo
    e = _email()
    created.append(e)
    ip = _ip()
    r1 = requests.post(URL, json={"email": e}, headers={"X-Forwarded-For": ip}, timeout=20)
    j1 = db.harness_waitlist.find_one({"email": e})["joined_at"]
    r2 = requests.post(URL, json={"email": e.upper()}, headers={"X-Forwarded-For": ip}, timeout=20)
    assert r1.status_code == 200 and r2.status_code == 200
    assert db.harness_waitlist.count_documents({"email": e}) == 1
    assert db.harness_waitlist.find_one({"email": e})["joined_at"] == j1


# Waitlist: malformed emails -> 400 with clear message
@pytest.mark.parametrize("bad", ["not-an-email", "a@b", "@example.com", "foo bar@x.com", ""])
def test_malformed_email_400(bad):
    r = requests.post(URL, json={"email": bad}, headers={"X-Forwarded-For": _ip()}, timeout=20)
    assert r.status_code == 400, f"{bad!r} -> {r.status_code} {r.text}"
    assert "valid email" in r.json()["detail"].lower()


# Waitlist: missing field -> 422 (pydantic)
def test_missing_email_422():
    r = requests.post(URL, json={}, headers={"X-Forwarded-For": _ip()}, timeout=20)
    assert r.status_code == 422


# Waitlist: no auth required (no Authorization header) + rate limit 429 on 6th request
def test_rate_limit_429(mongo):
    _, created = mongo
    ip = _ip()
    codes = []
    for _ in range(7):
        e = _email()
        created.append(e)
        codes.append(requests.post(URL, json={"email": e}, headers={"X-Forwarded-For": ip}, timeout=20).status_code)
    assert codes[:5] == [200] * 5, codes
    assert 429 in codes[5:], codes


# Fix 3: the 3 public GETs all respond 200 with expected shapes
def test_public_gets():
    ps = requests.get(f"{BASE_URL}/api/harness/page-status", timeout=20)
    assert ps.status_code == 200 and ps.json().get("harness_page_enabled") is True
    c = requests.get(f"{BASE_URL}/api/harness/content", timeout=20)
    assert c.status_code == 200
    body = c.json()
    assert "content" in body and "live_stats" in body
    txt = str(body["content"])
    for dead in ["Watch the 3-minute demo", "See the setup", "Compare with paid tools", "More FAQ"]:
        assert dead not in txt, f"dead link text '{dead}' in content JSON"
    rs = requests.get(f"{BASE_URL}/api/harness/release-status", timeout=20)
    assert rs.status_code == 200 and rs.json().get("available") is False
