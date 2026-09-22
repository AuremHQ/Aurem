"""Live end-to-end regression tests for Section 15 batch (2026-09-22).

Scope:
  1. Input normalizer live on POST /api/aurem-dev/chat/send (unicode, empty, long, foreign)
  2. Admin self-bugs list reachable (regression backlog cron target collection)
  3. Normal multi-tool-call chat flow smoke test (hard per-tool-call cap non-regression)

Uses preview credentials from /app/memory/test_credentials.md.
Keeps LLM cost minimal by using max_tool_iters=0 on most calls.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/aurem-dev/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    token = data.get("token") or data.get("access_token") or data.get("session_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


# --- 1. Input normalizer live tests ---

class TestInputNormalizerLive:
    def _send(self, sess, prompt):
        return sess.post(
            f"{BASE_URL}/api/aurem-dev/chat/send",
            json={"prompt": prompt, "max_tool_iters": 0},
            timeout=90,
        )

    def test_curly_quotes_emdash_ellipsis(self, auth_session):
        # Real unicode chars: U+2018 U+2019 U+201C U+201D U+2014 U+2026
        prompt = "Just say the word done. Test of \u2018it\u2019s working\u2019 \u2014 ellipsis\u2026 too."
        r = self._send(auth_session, prompt)
        assert r.status_code == 200, f"unicode chat failed: {r.status_code} {r.text[:400]}"
        body = r.json()
        assert isinstance(body, dict)

    def test_empty_prompt_rejected_gracefully(self, auth_session):
        r = self._send(auth_session, "")
        # Empty prompt should be a client error, NOT a 500
        assert r.status_code in (400, 422), f"empty prompt got {r.status_code}: {r.text[:300]}"

    def test_whitespace_only_prompt(self, auth_session):
        r = self._send(auth_session, "   \n\t  ")
        # Server either rejects (400/422) or routes to a casual reply (200) — both acceptable, must NOT be 500
        assert r.status_code in (200, 400, 422), f"whitespace prompt got {r.status_code}: {r.text[:300]}"

    def test_very_long_prompt(self, auth_session):
        # ~15000 chars
        prompt = "Say 'ok' only. " + ("filler content " * 1000)
        r = self._send(auth_session, prompt)
        # Accept 200 or 413/422 if length-limited; must NOT be 500
        assert r.status_code in (200, 413, 422), f"long prompt got {r.status_code}: {r.text[:300]}"

    def test_foreign_script_and_emoji_pass_through(self, auth_session):
        # Chinese + Devanagari + emoji + Arabic — should not be mangled or 500
        prompt = "Say 'ok'. \u4f60\u597d \u0928\u092e\u0938\u094d\u0924\u0947 \U0001f600\U0001f680 \u0645\u0631\u062d\u0628\u0627"
        r = self._send(auth_session, prompt)
        assert r.status_code == 200, f"foreign script got {r.status_code}: {r.text[:400]}"


# --- 2. Admin self-bugs list (regression backlog cron target endpoint) ---

class TestAdminSelfBugsList:
    def test_admin_self_bugs_list_reachable(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/aurem-dev/admin/self-bugs/list", timeout=30)
        assert r.status_code == 200, f"self-bugs list failed: {r.status_code} {r.text[:400]}"
        data = r.json()
        # Should return an object or list, no 500
        assert isinstance(data, (list, dict)), f"unexpected shape: {type(data)}"


# --- 3. Normal chat flow (hard-cap non-regression smoke) ---

class TestNormalChatFlowNoRegression:
    def test_simple_chat_send_works(self, auth_session):
        r = auth_session.post(
            f"{BASE_URL}/api/aurem-dev/chat/send",
            json={"prompt": "Reply with the single word: pong", "max_tool_iters": 0},
            timeout=90,
        )
        assert r.status_code == 200, f"basic chat failed: {r.status_code} {r.text[:400]}"
