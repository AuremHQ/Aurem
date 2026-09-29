"""
tests/test_activation_part3_guided_first_task.py — 2026-09-28
"ACTIVATION FIRST" Part 3 regression coverage.

Covers the guided-first-task endpoints added this session:
  - POST /onboarding/scan-website  (Firecrawl-down fallback path,
    since Firecrawl credits are confirmed exhausted in this env)
  - GET  /onboarding/demo-project
  - POST /onboarding/demo-project/run (LLM fix + subprocess-verified)

Requires BASE_URL env or falls back to localhost — run against the
live preview backend (auth required, uses a throwaway signup).
"""
import os
import random

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
API = f"{BASE_URL}/api/aurem-dev"


@pytest.fixture(scope="module")
def auth_headers():
    email = f"pytest-part3-{random.randint(100000, 999999)}@example.com"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": "PytestPart3!2026", "name": "Pytest",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}"}


class TestScanWebsite:
    def test_scan_returns_real_result_even_without_firecrawl(self, auth_headers):
        r = requests.post(f"{API}/onboarding/scan-website",
                           json={"url": "example.com"}, headers=auth_headers, timeout=25)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["scan_path"] in ("firecrawl", "fallback")
        assert isinstance(data["issues"], list)
        assert "issue_count" in data

    def test_scan_rejects_garbage_url(self, auth_headers):
        r = requests.post(f"{API}/onboarding/scan-website",
                           json={"url": "not a url at all"}, headers=auth_headers, timeout=10)
        assert r.status_code == 400

    def test_scan_blocks_internal_addresses(self, auth_headers):
        r = requests.post(f"{API}/onboarding/scan-website",
                           json={"url": "http://169.254.169.254/latest/meta-data/"},
                           headers=auth_headers, timeout=10)
        assert r.status_code == 400

    def test_scan_requires_auth(self):
        r = requests.post(f"{API}/onboarding/scan-website",
                           json={"url": "example.com"}, timeout=10)
        assert r.status_code in (401, 403)


class TestDemoProject:
    def test_demo_project_info(self, auth_headers):
        r = requests.get(f"{API}/onboarding/demo-project", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "calculate_order_total" in data["source"]

    def test_demo_project_run_ships_a_verified_fix(self, auth_headers):
        r = requests.post(f"{API}/onboarding/demo-project/run",
                           headers=auth_headers, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["verified"] is True, "demo fix must verify (LLM path or known-good fallback)"
        assert data["after_result"] == 50.0
        assert "+" in data["diff"] and "-" in data["diff"]
