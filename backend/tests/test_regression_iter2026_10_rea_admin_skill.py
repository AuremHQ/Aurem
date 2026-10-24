"""tests/test_regression_iter2026_10_rea_admin_skill.py

Admin-only `/reverse-engineer <url>` ORA chat skill (github.com/morluto/rea).
Routes: POST /api/aurem-dev/admin/rea/analyze,
        GET  /api/aurem-dev/admin/rea/jobs/{job_id},
        GET  /api/aurem-dev/admin/rea/jobs/{job_id}/file/{sha256}

Covers: SSRF rejection, 403 non-admin, 401 anon, 409 concurrent job,
job-status response hides file_text_by_sha256, file endpoint 404s,
and one live happy-path round trip on https://example.com.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"
ADMIN = {"email": "test@aurem.dev", "password": "AuremTest2026!"}


@pytest.fixture(scope="module")
def admin_headers():
    r = requests.post(f"{API}/auth/login", json=ADMIN, timeout=30)
    if r.status_code != 200 or not r.json().get("token"):
        pytest.skip(f"admin login failed: {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def nonadmin_headers():
    email = f"test_rea_nonadmin_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{API}/auth/signup",
                      json={"email": email, "password": "NonAdminRea2026!x", "name": "TEST Rea"},
                      timeout=30)
    if r.status_code != 200 or not r.json().get("token"):
        pytest.skip(f"signup failed: {r.status_code} {r.text[:200]}")
    assert r.json().get("is_admin") is False
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── Unit: SSRF validator (no network / no Chromium) ──────────────────
class TestValidateTargetUrl:
    @pytest.mark.parametrize("bad", [
        "http://localhost:8001/api/healthz", "http://127.0.0.1", "http://0.0.0.0",
        "http://10.0.0.5", "http://192.168.1.1", "http://169.254.169.254/latest/meta-data",
        "http://[::1]/", "ftp://example.com", "",
    ])
    def test_rejects_unsafe(self, bad):
        from services.rea_service import ReaValidationError, validate_target_url
        with pytest.raises(ReaValidationError):
            validate_target_url(bad)

    def test_accepts_public_and_adds_scheme(self):
        from services.rea_service import validate_target_url
        assert validate_target_url("https://example.com") == "https://example.com"
        assert validate_target_url("example.com") == "https://example.com"


# ── Gating ───────────────────────────────────────────────────────────
class TestGating:
    def test_anonymous_rejected(self):
        r = requests.post(f"{API}/admin/rea/analyze", json={"url": "https://example.com"}, timeout=30)
        assert r.status_code in (401, 403)

    def test_nonadmin_403(self, nonadmin_headers):
        r = requests.post(f"{API}/admin/rea/analyze", json={"url": "https://example.com"},
                          headers=nonadmin_headers, timeout=30)
        assert r.status_code == 403
        assert "admin" in r.json().get("detail", "").lower()

    def test_nonadmin_cannot_read_jobs(self, nonadmin_headers):
        r = requests.get(f"{API}/admin/rea/jobs/rea_doesnotexist", headers=nonadmin_headers, timeout=30)
        assert r.status_code == 403


# ── SSRF via API ─────────────────────────────────────────────────────
class TestSsrfApi:
    @pytest.mark.parametrize("bad", [
        "http://localhost:8001/api/healthz", "http://127.0.0.1:27017", "http://169.254.169.254/",
    ])
    def test_private_hosts_400(self, admin_headers, bad):
        r = requests.post(f"{API}/admin/rea/analyze", json={"url": bad}, headers=admin_headers, timeout=30)
        assert r.status_code == 400, r.text
        assert "blocked" in r.json()["detail"].lower()


# ── 404s ─────────────────────────────────────────────────────────────
class TestNotFound:
    def test_unknown_job_404(self, admin_headers):
        r = requests.get(f"{API}/admin/rea/jobs/rea_nope_{uuid.uuid4().hex[:6]}", headers=admin_headers, timeout=30)
        assert r.status_code == 404

    def test_unknown_job_file_404(self, admin_headers):
        r = requests.get(f"{API}/admin/rea/jobs/rea_nope/file/{'0' * 64}", headers=admin_headers, timeout=30)
        assert r.status_code == 404


# ── Live round trip + 409 concurrency (real Chromium + rea CLI) ──────
class TestLiveRoundTrip:
    def test_full_flow_example_com(self, admin_headers):
        r = requests.post(f"{API}/admin/rea/analyze", json={"url": "https://example.com"},
                          headers=admin_headers, timeout=30)
        if r.status_code == 409:
            pytest.skip(f"admin already has a running job: {r.text}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "running" and data["url"] == "https://example.com"
        job_id = data["job_id"]
        assert job_id.startswith("rea_")

        # 409 while still running
        r2 = requests.post(f"{API}/admin/rea/analyze", json={"url": "https://example.org"},
                           headers=admin_headers, timeout=30)
        assert r2.status_code == 409, r2.text
        assert job_id in r2.json()["detail"] and "example.com" in r2.json()["detail"]

        # poll
        deadline, job = time.time() + 180, None
        while time.time() < deadline:
            job = requests.get(f"{API}/admin/rea/jobs/{job_id}", headers=admin_headers, timeout=30).json()
            if job["status"] != "running":
                break
            time.sleep(3)
        assert job and job["status"] == "done", job
        assert "file_text_by_sha256" not in job
        assert "_id" not in job
        assert job["backend_note"] and "never sent to the browser" in job["backend_note"]
        assert isinstance(job["observed_api_endpoints"], list)
        html = [f for f in job["files"] if f["type"] == "html"]
        assert html, job["files"]

        fr = requests.get(f"{API}/admin/rea/jobs/{job_id}/file/{html[0]['sha256']}",
                          headers=admin_headers, timeout=30)
        assert fr.status_code == 200
        assert "<title>Example Domain</title>" in fr.json()["text"]

        bad = requests.get(f"{API}/admin/rea/jobs/{job_id}/file/{'f' * 64}", headers=admin_headers, timeout=30)
        assert bad.status_code == 404
