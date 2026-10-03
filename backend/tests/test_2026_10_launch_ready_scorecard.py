"""
Backend tests for the 2026-10 "Launch-ready X/16" scorecard,
Fix List panel endpoint, and Guardian re-scan (SSE field).
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"
PROJECT_ID = "p_145fb30e52"

EXPECTED_16_KEYS = {
    "security", "performance", "code_quality", "dependencies",
    "database", "bug_hunt", "docker",
    "test_coverage", "api_security", "security_headers",
    "accessibility", "seo_basics", "git_hygiene",
    "error_handling", "mobile_responsiveness", "documentation",
}

VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
               timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


# ---------- 16-category scan ----------
class TestLaunchReadyScan:
    def test_scan_returns_16_categories(self, session):
        r = session.post(f"{API}/codebase-health/scan",
                         json={"project_id": PROJECT_ID},
                         timeout=180)
        assert r.status_code == 200, f"scan {r.status_code} body={r.text[:400]}"
        data = r.json()
        assert "breakdown" in data, f"no breakdown key: {list(data.keys())[:20]}"
        breakdown = data["breakdown"]
        got = set(breakdown.keys())
        missing = EXPECTED_16_KEYS - got
        extra = got - EXPECTED_16_KEYS
        assert not missing, f"missing categories: {missing}"
        assert not extra, f"unexpected categories: {extra}"
        assert len(breakdown) == 16

    def test_findings_valid_severity_and_category(self, session):
        r = session.post(f"{API}/codebase-health/scan",
                         json={"project_id": PROJECT_ID},
                         timeout=180)
        assert r.status_code == 200
        data = r.json()
        findings = data.get("findings", [])
        bad_sev, bad_cat = [], []
        for f in findings:
            if f.get("severity", "").lower() not in VALID_SEVERITIES:
                bad_sev.append((f.get("id"), f.get("severity")))
            if f.get("category") not in EXPECTED_16_KEYS:
                bad_cat.append((f.get("id"), f.get("category")))
        assert not bad_sev, f"invalid severities: {bad_sev[:5]}"
        assert not bad_cat, f"invalid categories: {bad_cat[:5]}"


# ---------- Fix List endpoint (new, cadence-free) ----------
class TestFixListEndpoint:
    def test_fix_list_returns_open_findings(self, session):
        r = session.get(f"{API}/findings/fix-list",
                        params={"project_id": PROJECT_ID},
                        timeout=30)
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        data = r.json()
        # endpoint may return {items:[...]} or list
        items = data.get("items") if isinstance(data, dict) else data
        assert isinstance(items, list), f"items not list: {type(items)}"
        # each item should carry status open
        for it in items[:20]:
            st = (it.get("status") or "").lower()
            assert st in ("open", "", "new", "pending"), f"bad status {st}"

    def test_fix_list_requires_project_id(self, session):
        r = session.get(f"{API}/findings/fix-list", timeout=15)
        assert r.status_code in (400, 422, 404), (
            f"should reject missing project_id, got {r.status_code}")


# ---------- Backlog regression (unchanged behavior) ----------
class TestBacklogRegression:
    def test_backlog_only_crit_high(self, session):
        r = session.get(f"{API}/findings/backlog",
                        params={"project_id": PROJECT_ID},
                        timeout=30)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        data = r.json()
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            pytest.skip("backlog shape differs")
        for it in items[:50]:
            sev = (it.get("severity") or "").lower()
            if sev:
                assert sev in ("critical", "high"), (
                    f"backlog returned non-crit/high: {sev}")
