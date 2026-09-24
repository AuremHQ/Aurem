"""Backend regression for prompt-caching fix (Section 16).

Flag LLM_PROMPT_CACHING_ENABLED=0 (default). We only verify that normal
chat/send flows still work end-to-end and no traceback surfaces.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    tok = data.get("token") or data.get("access_token") or data.get("jwt")
    assert tok, f"no token in login response: {list(data.keys())}"
    return tok


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _send(headers, prompt, extra=None, timeout=180):
    payload = {"prompt": prompt}
    if extra:
        payload.update(extra)
    return requests.post(f"{API}/chat/send", json=payload, headers=headers, timeout=timeout)


# --- Test 1: normal single-turn chat (iters=1, cache_system=False branch) ---
def test_normal_single_turn_chat(headers):
    r = _send(headers, "Just say the word done and nothing else.")
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:400]}"
    body = r.json()
    # Look for reply text under any of several common keys.
    reply = (
        body.get("reply")
        or body.get("response")
        or body.get("message")
        or body.get("content")
        or (body.get("data") or {}).get("reply")
        or ""
    )
    assert isinstance(body, dict)
    assert reply or body, f"empty reply body: {body}"
    print(f"[t1] status=200, keys={list(body.keys())[:8]}, reply_len={len(str(reply))}")


# --- Test 2: multi-iteration prompt likely to trigger tool calls (iters >= 2 → cache_system=True branch) ---
def test_multi_iteration_tool_chat(headers):
    r = _send(
        headers,
        "List the files in the backend/services directory, then tell me how many .py files there are.",
        timeout=240,
    )
    # We don't require a specific status_code semantic — the goal is: no 500 traceback.
    assert r.status_code in (200,), f"got {r.status_code}: {r.text[:400]}"
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    print(f"[t2] status={r.status_code}, top_keys={list(body.keys())[:8] if isinstance(body, dict) else type(body)}")


# --- Test 3: pre-existing bug reproduction: max_tool_iters=0 → 500 UnboundLocalError ---
def test_max_tool_iters_zero_preexisting_bug(headers):
    r = _send(headers, "Say hi.", extra={"max_tool_iters": 0}, timeout=60)
    # Confirming pre-existing bug still reproduces (500). If it silently changed,
    # we flag it but do NOT fail the fix regression suite.
    print(f"[t3-preexisting] status={r.status_code}, body_head={r.text[:200]}")
    # Not asserting a specific status — just recording behavior for the report.
    assert r.status_code in (200, 400, 422, 500, 502, 504), f"unexpected status {r.status_code}"


# --- Test 4: 2-3 sequential ordinary chat turns (session regression smoke) ---
@pytest.mark.parametrize("i,msg", [
    (1, "Reply with just: one"),
    (2, "Reply with just: two"),
    (3, "Reply with just: three"),
])
def test_sequential_chat_turns(headers, i, msg):
    r = _send(headers, msg, timeout=120)
    assert r.status_code == 200, f"turn {i} got {r.status_code}: {r.text[:300]}"
    time.sleep(1)
    print(f"[t4.{i}] ok")
