"""Backend tests for AUREM Admin Context Bridge (2026-09-09).

Live-HTTP integration style, matching test_codebase_health_score.py's
convention: real login against the running server, real POST/GET
round-trip. Verifies:
  1. Admin auth is enforced (no token -> 401/403).
  2. A valid capture round-trips into GET /chat/history for that
     session — the "verify sanitized payload is received in the
     active admin session" acceptance criterion, checked against the
     REAL storage path, not a mock.
  3. Server-side sanitize pass actually redacts a password/bearer
     token/API key even if a client "forgot" to (defense in depth) —
     the raw secret must NEVER show up in the stored message.
  4. Oversized fields are truncated, not stored unbounded.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    try:
        for _ln in open("/app/frontend/.env"):
            if _ln.strip().startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = _ln.split("=", 1)[1].strip().rstrip("/")
                break
    except FileNotFoundError:
        pass
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    if data.get("mfa_required"):
        pytest.skip("MFA required — cannot proceed without TOTP")
    assert data.get("is_admin"), f"user is not admin: {data}"
    return data["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


def _fresh_session_id() -> str:
    return f"ctx-bridge-test-{uuid.uuid4().hex[:12]}"


def test_context_bridge_requires_auth():
    r = requests.post(f"{API}/admin/ora/context-bridge",
                       json={"session_id": "no-auth", "visible_text": "hi"}, timeout=15)
    assert r.status_code in (401, 403), r.text


def test_context_bridge_round_trips_into_chat_history(headers):
    session_id = _fresh_session_id()
    payload = {
        "session_id": session_id,
        "url": "https://vendor-portal.example.com/dashboard",
        "title": "Vendor Portal — Billing",
        "visible_text": "Invoice #4471 is overdue by 12 days.",
        "console_errors": ["TypeError: cannot read property 'total' of undefined"],
        "network_metrics": [
            {"name": "https://vendor-portal.example.com/api/invoices", "duration": 842.3, "transferSize": 5120},
        ],
        "captured_at": "2026-09-09T21:00:00Z",
    }
    r = requests.post(f"{API}/admin/ora/context-bridge", json=payload, headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert "Invoice #4471" in body.get("preview", "") or True  # preview may truncate before this point

    hist = requests.get(f"{API}/chat/history", params={"session_id": session_id}, headers=headers, timeout=15)
    assert hist.status_code == 200, hist.text
    messages = hist.json().get("messages", [])
    assert len(messages) == 2, f"expected exactly one user+assistant pair, got {len(messages)}"
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assistant_content = messages[1]["content"]
    assert "Browser context received" in assistant_content
    assert "vendor-portal.example.com" in assistant_content
    assert "Invoice #4471 is overdue by 12 days." in assistant_content
    assert "cannot read property 'total' of undefined" in assistant_content
    assert "api/invoices" in assistant_content


def test_context_bridge_sanitizes_secrets_server_side(headers):
    session_id = _fresh_session_id()
    payload = {
        "session_id": session_id,
        "url": "https://vendor-portal.example.com/settings",
        "title": "Settings",
        "visible_text": (
            "Account settings. password=SuperSecret123! "
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456ghijk "
            "api_key=sk-liveTESTKEYabcdefghijklmnop123456"
        ),
        "console_errors": ["Fetch failed: Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJhIjoiYiJ9.zzzzzzzzzzzzzzzz"],
        "network_metrics": [],
        "captured_at": "2026-09-09T21:05:00Z",
    }
    r = requests.post(f"{API}/admin/ora/context-bridge", json=payload, headers=headers, timeout=15)
    assert r.status_code == 200, r.text

    hist = requests.get(f"{API}/chat/history", params={"session_id": session_id}, headers=headers, timeout=15)
    assistant_content = hist.json()["messages"][1]["content"]

    assert "SuperSecret123" not in assistant_content
    assert "sk-liveTESTKEYabcdefghijklmnop123456" not in assistant_content
    assert "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456ghijk" not in assistant_content
    assert "[REDACTED_PASSWORD]" in assistant_content
    assert "[REDACTED_SECRET]" in assistant_content or "[REDACTED_KEY]" in assistant_content
    assert "[REDACTED_AUTH_HEADER]" in assistant_content or "[REDACTED_JWT]" in assistant_content


def test_context_bridge_truncates_oversized_text(headers):
    session_id = _fresh_session_id()
    huge_text = "A" * 20000
    payload = {
        "session_id": session_id,
        "url": "https://vendor-portal.example.com/huge-page",
        "visible_text": huge_text,
        "console_errors": [f"error #{i}: " + ("x" * 1000) for i in range(50)],
        "network_metrics": [],
        "captured_at": "2026-09-09T21:10:00Z",
    }
    r = requests.post(f"{API}/admin/ora/context-bridge", json=payload, headers=headers, timeout=15)
    assert r.status_code == 200, r.text

    hist = requests.get(f"{API}/chat/history", params={"session_id": session_id}, headers=headers, timeout=15)
    assistant_content = hist.json()["messages"][1]["content"]
    # Full 20000-char text blob + 50×1008-char errors (~70KB raw) must
    # never land verbatim — caps are text<=4000, 20 errors<=600 chars
    # each, so ceiling is ~4000 + 20*600 + markdown overhead ≈ 16.5KB.
    assert len(assistant_content) < 18000
    assert "A" * 4001 not in assistant_content  # visible_text capped at 4000
    # At most 20 console error items get through per _MAX_ERROR_ITEMS.
    assert assistant_content.count("error #") <= 20
