# Live HTTP regression for UI mission Tickets D, C, E, A, F (2026-02) — hits the public preview URL.
import os
import time
import uuid
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/") + "/api/aurem-dev"
PID = "p_145fb30e52"
EMAIL, PWD = "test@aurem.dev", "AuremTest2026!"


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{BASE_URL}/auth/login", json={"email": EMAIL, "password": PWD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}")
    return r.json()["token"]


@pytest.fixture(scope="session")
def s(token):
    ss = requests.Session()
    ss.headers.update({"Authorization": f"Bearer {token}"})
    return ss


@pytest.fixture(scope="session")
def db():
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


# ── Ticket E: trend endpoint ──
class TestTrend:
    def test_trend_with_history(self, s):
        r = s.get(f"{BASE_URL}/codebase-health/trend", params={"project_id": PID}, timeout=30)
        assert r.status_code == 200
        snaps = r.json().get("snapshots")
        assert isinstance(snaps, list) and len(snaps) >= 2
        assert all("score" in x and "snapshot_at" in x for x in snaps)

    def test_trend_no_history_is_empty(self, s):
        r = s.get(f"{BASE_URL}/codebase-health/trend", params={"project_id": "p_demo_b"}, timeout=30)
        assert r.status_code == 200
        assert r.json().get("snapshots") == []


# ── Ticket C: PDF ──
class TestPdf:
    def test_pdf_owner_ok(self, s):
        r = s.get(f"{BASE_URL}/cto/projects/{PID}/report.pdf", timeout=90)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF"

    def test_pdf_unauth_401(self):
        r = requests.get(f"{BASE_URL}/cto/projects/{PID}/report.pdf", timeout=30)
        assert r.status_code == 401

    def test_pdf_unknown_404(self, s):
        r = s.get(f"{BASE_URL}/cto/projects/p_does_not_exist_x/report.pdf", timeout=30)
        assert r.status_code == 404


# ── Ticket A: email health + first-scan prompt ──
class TestAutoScan:
    def test_email_status_reports_broken(self, s):
        r = s.get(f"{BASE_URL}/cto/email-delivery-status", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["healthy"] is False
        assert d["reason"]

    def test_first_scan_signal_one_time(self, s, db):
        src = db.cto_projects.find_one({"project_id": PID}, {"_id": 0})
        new_id = f"p_TEST_firstscan_{uuid.uuid4().hex[:6]}"
        doc = dict(src)
        doc.update({"project_id": new_id, "name": f"TEST_FirstScan_{new_id}",
                    "auto_scan_prompt_dismissed": False, "auto_scan_frequency": "off",
                    "created_at": time.time()})
        db.cto_projects.insert_one(doc)
        try:
            r1 = s.post(f"{BASE_URL}/codebase-health/scan", json={"project_id": new_id}, timeout=120)
            assert r1.status_code == 200, r1.text[:300]
            d1 = r1.json()
            assert d1["is_first_scan"] is True
            assert d1["auto_scan_frequency"] == "off"
            assert d1["auto_scan_prompt_dismissed"] is False
            r2 = s.post(f"{BASE_URL}/codebase-health/scan", json={"project_id": new_id}, timeout=120)
            assert r2.status_code == 200
            assert r2.json()["is_first_scan"] is False
            # PATCH dismiss + weekly persists
            p = s.patch(f"{BASE_URL}/cto/projects/{new_id}",
                        json={"auto_scan_prompt_dismissed": True, "auto_scan_frequency": "weekly"}, timeout=30)
            assert p.status_code == 200, p.text[:300]
            got = db.cto_projects.find_one({"project_id": new_id})
            assert got["auto_scan_prompt_dismissed"] is True
            assert got["auto_scan_frequency"] == "weekly"
        finally:
            db.cto_projects.delete_one({"project_id": new_id})
            db.codebase_health_scans.delete_many({"project_id": new_id})
            db.project_health_snapshots.delete_many({"project_id": new_id})


# ── Ticket F: simplify safety gate ──
class TestSimplify:
    def test_preview_test_file_refused(self, s):
        r = s.post(f"{BASE_URL}/cto/projects/{PID}/simplify",
                   json={"path": "backend/tests/test_something.py"}, timeout=60)
        assert r.status_code == 422
        d = r.json()["detail"]
        assert d.get("refused") is True
        assert isinstance(d.get("reason"), str) and d["reason"]

    def test_apply_test_file_refused(self, s):
        r = s.post(f"{BASE_URL}/cto/projects/{PID}/simplify/apply",
                   json={"path": "tests/test_x.py", "new_content": "x = 1\n"}, timeout=60)
        assert r.status_code == 422
        assert r.json()["detail"].get("refused") is True

    def test_preview_unauth(self):
        r = requests.post(f"{BASE_URL}/cto/projects/{PID}/simplify", json={"path": "a.py"}, timeout=30)
        assert r.status_code == 401

    def test_preview_real_file_honest(self, s):
        r = s.post(f"{BASE_URL}/cto/projects/{PID}/simplify", json={"path": "math_utils.py"}, timeout=120)
        # either a real diff (200 ok) or an honest error/refusal — never 200 with ok:false
        assert r.status_code in (200, 422, 502, 404), r.text[:300]
        if r.status_code == 200:
            d = r.json()
            assert d.get("ok") is True
            assert "diff" in d or d.get("unchanged")


# ── Ticket D: proof meta ──
class TestProof:
    def test_meta_success_job(self, s):
        r = s.get(f"{BASE_URL}/fix-pipeline/proof/demo_proof_55a6b45e77/meta", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["attempted"] is True
        assert d["devices"]["desktop"]["has_images"] is True

    def test_meta_ssrf_job_could_not_verify(self, s):
        r = s.get(f"{BASE_URL}/fix-pipeline/proof/demo_proof_ssrf/meta", timeout=30)
        d = r.json()
        assert d["attempted"] is True
        assert d["devices"]["desktop"]["has_images"] is False
        assert d["devices"]["mobile"]["has_images"] is False

    def test_meta_unknown_job(self, s):
        r = s.get(f"{BASE_URL}/fix-pipeline/proof/nope_job_zzz/meta", timeout=30)
        assert r.status_code == 200
        assert r.json()["attempted"] is False

    def test_image_bytes(self, s):
        r = s.get(f"{BASE_URL}/fix-pipeline/proof/demo_proof_55a6b45e77",
                  params={"variant": "before", "device": "desktop"}, timeout=30)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("image/")

    def test_meta_unauth(self):
        r = requests.get(f"{BASE_URL}/fix-pipeline/proof/demo_proof_55a6b45e77/meta", timeout=30)
        assert r.status_code == 401
