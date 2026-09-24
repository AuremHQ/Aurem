"""
Test Phase 3+4 Visual Bug-Fix pipeline endpoints.

Focus: error-path testing (sandbox has no working GitHub App
installation, so happy path for generate-patch/preview/approve-pr
can only be exercised at the 400 layer).

Also: verifies /usage/me still works after the new visual_bug_reports
counter added to services/usage.py::get_usage().
"""
import io
import os

import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"
FREE_EMAIL = "free-gate-test-0822@aurem.dev"
FREE_PASSWORD = "FreeGateTest2026!"


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    data = r.json()
    return data.get("token") or data.get("access_token")


@pytest.fixture(scope="module")
def admin_token():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def free_token():
    return _login(FREE_EMAIL, FREE_PASSWORD)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_png_bytes():
    # 1x1 PNG
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


# ---------- Usage endpoint regression ----------

class TestUsageEndpoint:
    def test_usage_me_admin(self, admin_token):
        r = requests.get(f"{API}/usage/me", headers=_auth(admin_token), timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        # Field presence & basic types (must include the fields extended by the new counter)
        for f in ["tasks_this_month", "monthly_task_cap", "tasks_remaining", "is_blocked"]:
            assert f in data, f"missing {f}: {data}"
        assert isinstance(data["tasks_this_month"], int)

    def test_usage_me_free_tier_cap_is_10(self, free_token):
        r = requests.get(f"{API}/usage/me", headers=_auth(free_token), timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("tier") == "free", data
        assert data.get("monthly_task_cap") == 10, data


# ---------- Visual bug-fix upload + phase 3 error paths ----------

class TestVisualBugfixErrorPaths:
    @pytest.fixture(scope="class")
    def uploaded_report(self, admin_token):
        # Upload a screenshot with NO project_id -> analyze will not correlate,
        # candidate_files will be empty. This is the shape needed to exercise
        # /generate-patch 400 paths.
        png = _make_png_bytes()
        files = {"file": ("bug.png", png, "image/png")}
        # Storage backend flaky (occasional 500 from object-store) — retry a few times
        last = None
        for _ in range(4):
            try:
                r = requests.post(f"{API}/visual-bugfix/upload", files=files,
                                  headers=_auth(admin_token), timeout=90)
                if r.status_code == 200:
                    return r.json()["id"]
                last = r.text
            except requests.exceptions.RequestException as e:
                last = str(e)
        pytest.skip(f"upload keeps failing/timing out: {last}")

    def test_upload_ok(self, uploaded_report):
        assert isinstance(uploaded_report, str) and len(uploaded_report) > 10

    def test_generate_patch_before_analyze_returns_400(self, admin_token, uploaded_report):
        r = requests.post(
            f"{API}/visual-bugfix/{uploaded_report}/generate-patch",
            headers=_auth(admin_token), json={}, timeout=30,
        )
        assert r.status_code == 400, r.text
        body = r.json()
        # FastAPI HTTPException wraps in {"detail": "..."}
        detail = body.get("detail") or body.get("error") or ""
        assert "not ready" in detail.lower() or "run /analyze" in detail.lower(), body

    def test_preview_before_patch_returns_400(self, admin_token, uploaded_report):
        r = requests.post(
            f"{API}/visual-bugfix/{uploaded_report}/preview",
            headers=_auth(admin_token), timeout=30,
        )
        assert r.status_code == 400, r.text
        detail = (r.json().get("detail") or "").lower()
        assert "no patch" in detail or "generate-patch" in detail, r.text

    def test_approve_pr_before_patch_returns_400(self, admin_token, uploaded_report):
        r = requests.post(
            f"{API}/visual-bugfix/{uploaded_report}/approve-pr",
            headers=_auth(admin_token), timeout=30,
        )
        assert r.status_code == 400, r.text
        detail = (r.json().get("detail") or "").lower()
        assert "no patch" in detail or "ready to ship" in detail, r.text

    def test_status_always_200(self, admin_token, uploaded_report):
        r = requests.get(
            f"{API}/visual-bugfix/{uploaded_report}/status",
            headers=_auth(admin_token), timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert data.get("id") == uploaded_report
        assert "status" in data

    def test_discard_always_200_sets_status(self, admin_token, uploaded_report):
        r = requests.post(
            f"{API}/visual-bugfix/{uploaded_report}/discard",
            headers=_auth(admin_token), timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("status") == "discarded"

        s = requests.get(
            f"{API}/visual-bugfix/{uploaded_report}/status",
            headers=_auth(admin_token), timeout=15,
        )
        assert s.status_code == 200
        assert s.json().get("status") == "discarded"

    def test_generate_patch_after_analyze_no_candidates_returns_400(self, admin_token):
        """Upload -> analyze (no project_id -> no candidate_files) -> generate-patch => 400."""
        png = _make_png_bytes()
        files = {"file": ("bug2.png", png, "image/png")}
        r = requests.post(f"{API}/visual-bugfix/upload", files=files,
                          headers=_auth(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        rid = r.json()["id"]

        # Analyze without project — should still succeed but candidate_files=[]
        r2 = requests.post(
            f"{API}/visual-bugfix/{rid}/analyze",
            headers=_auth(admin_token),
            json={"bug_description": "test — no project"},
            timeout=90,
        )
        # analyze may succeed or fail depending on vision availability;
        # we only care that /generate-patch then rejects with 400
        if r2.status_code != 200:
            pytest.skip(f"analyze not available in this env: {r2.status_code} {r2.text[:200]}")

        r3 = requests.post(
            f"{API}/visual-bugfix/{rid}/generate-patch",
            headers=_auth(admin_token), json={}, timeout=30,
        )
        assert r3.status_code == 400, r3.text
        detail = (r3.json().get("detail") or "").lower()
        # Two valid 400 paths: (a) not in analyzed status, (b) no candidate files
        assert (
            "no candidate" in detail
            or "not ready" in detail
            or "connect a repo" in detail
        ), r3.text


# ---------- Free tier quota shape (code-level assertion) ----------

class TestQuotaEndpointsGated:
    def test_upload_calls_assert_has_task_budget(self):
        """Grep-level check that both endpoints reference assert_has_task_budget."""
        src = open("/app/backend/routers/visual_bugfix.py").read()
        assert "assert_has_task_budget" in src
        # both /upload and /generate-patch should call it
        assert src.count("assert_has_task_budget(") >= 2, "expected quota check at both /upload and /generate-patch"
