# Live (public URL) regression tests for Aurem Loop /harness Phase A: page-status, content proxy, live stats, /both redirect.
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")


# page-status: public, flag default OFF (unless EXPECT_HARNESS_ON=1)
def test_page_status_public():
    r = requests.get(f"{BASE_URL}/api/harness/page-status", timeout=20)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["harness_page_enabled"], bool)
    expected = os.environ.get("EXPECT_HARNESS_ON") == "1"
    assert body["harness_page_enabled"] is expected


# content: fail-closed + live stars/forks numbers
def test_content_fail_closed_with_live_stats():
    r = requests.get(f"{BASE_URL}/api/harness/content", timeout=30)
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == {"available": False}
    ls = body["live_stats"]
    assert isinstance(ls, dict)
    assert isinstance(ls["stars"], int) and ls["stars"] >= 0
    assert isinstance(ls["forks"], int) and ls["forks"] >= 0


# content: no auth required, no secret leak
def test_content_no_token_leak():
    r = requests.get(f"{BASE_URL}/api/harness/content", timeout=30)
    t = r.text
    for p in ("ghp_", "github_pat_", "AUREM_LOOP_REPO_TOKEN"):
        assert p not in t


# /both 301 -> /
def test_both_redirect():
    r = requests.get(f"{BASE_URL}/both", allow_redirects=False, timeout=20)
    assert r.status_code == 301
    assert r.headers["location"].rstrip("/").endswith(BASE_URL.split("//")[1]) or r.headers["location"] == "/"


# regression: marketing pages return 200
@pytest.mark.parametrize("path", ["/", "/harness", "/vs", "/compare", "/about"])
def test_marketing_pages_load(path):
    r = requests.get(f"{BASE_URL}{path}", timeout=30)
    assert r.status_code == 200
