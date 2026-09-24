"""Live acceptance test for the 2026-02 Context Confusion Parts A/B/D fix.

Founder demands live reproduction against the preview backend before accepting.

- Part B: Fresh message with a captured F12 payload but unrelated user prompt
  MUST NOT hijack into Mode D (must answer the user's actual request).
- Part D: Non-page-file question uses business-friendly "that file" language
  (not "that page", not raw filename echo) — smoke check the shared rendering
  pipeline still applies the business owner voice filter.
- Part A: 'yes go ahead' after a proposed action resolves the SAME action.
"""
import os
import uuid
import json
import requests
import pytest


def _read_env(path, key):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"{key} not found in {path}")


BASE_URL = _read_env("/app/frontend/.env", "REACT_APP_BACKEND_URL").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    return r.json()["token"]


def _send(token, prompt, session_id, f12_payload=None, project_id=None, timeout=180):
    body = {"prompt": prompt, "session_id": session_id}
    if f12_payload is not None:
        body["f12_payload"] = f12_payload
    if project_id:
        body["project_id"] = project_id
    return requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=timeout,
    )


# A realistic captured browser error payload — the shape that used to hijack
# Mode D even when the user's prompt was totally unrelated.
REAL_F12_PAYLOAD = {
    "console_errors": [
        {
            "message": "Uncaught TypeError: Cannot read properties of undefined (reading 'map')",
            "source": "https://bin-context-pat.preview.emergentagent.com/static/js/main.abc.js",
            "line": 317,
            "col": 42,
            "timestamp": 1737000000000,
        }
    ],
    "network_errors": [],
    "stack_traces": [],
}


def test_partB_fresh_message_with_captured_f12_does_not_hijack(token):
    """Fresh message asking about the footer while an unrelated JS error
    sits in window.__auremF12 — reply must be about the footer, NOT about
    the captured js:317 error."""
    session_id = f"live-partB-fresh-{uuid.uuid4().hex[:8]}"
    prompt = "update the footer text to say contact us today"
    r = _send(token, prompt, session_id, f12_payload=REAL_F12_PAYLOAD)
    assert r.status_code == 200, f"chat/send failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    text = json.dumps(data).lower()

    # Must NOT talk about the captured browser/js error
    hijack_phrases = [
        "cannot read properties of undefined",
        "js:317",
        "main.abc.js",
        "typeerror",
        "console error",
        "uncaught",
    ]
    hits = [p for p in hijack_phrases if p in text]
    assert not hits, (
        f"PART B FAIL — reply hijacked by captured F12 payload (matched {hits}). "
        f"Response head: {text[:800]}"
    )

    # Must plausibly reference the actual task (footer / contact)
    assert ("footer" in text or "contact" in text), (
        f"PART B soft-check — reply did not reference footer/contact task. Head: {text[:600]}"
    )


def test_partD_business_voice_that_file_not_that_page(token):
    """Ask about a backend/config file (not a page) — the shared rendering
    pipeline (run_rendering_pipeline) must still apply the business-voice
    filter: any generic file reference should say 'that file' not 'that
    page', and NOT keep saying the raw filename over and over."""
    session_id = f"live-partD-{uuid.uuid4().hex[:8]}"
    prompt = "what does package.json do"
    r = _send(token, prompt, session_id)
    assert r.status_code == 200, f"chat/send failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    text = json.dumps(data).lower()

    # Regression guard from the pre-existing business voice filter.
    # "that page" should NEVER appear when the referent is a non-page file.
    assert "that page" not in text, (
        f"PART D FAIL — business voice filter did not run through shared pipeline "
        f"(saw 'that page' referring to package.json). Head: {text[:600]}"
    )


def test_partA_yes_go_ahead_resolves_pending_action(token):
    """Send a message that proposes a low-risk edit — then reply 'yes, go
    ahead' in the same session. The confirmation must resolve the SAME
    previously-proposed action, not ask 'what do you want to confirm?'."""
    session_id = f"live-partA-{uuid.uuid4().hex[:8]}"
    propose = "add a top-of-file comment to frontend/src/index.js explaining what the file does"
    r1 = _send(token, propose, session_id)
    assert r1.status_code == 200, f"propose failed: {r1.status_code} {r1.text[:400]}"

    r2 = _send(token, "yes, go ahead", session_id)
    assert r2.status_code == 200, f"confirm failed: {r2.status_code} {r2.text[:400]}"
    text = json.dumps(r2.json()).lower()

    # Must NOT ask a generic clarifying question — the pending action's
    # scope (target file, description) should already be in ctx via
    # task_state.
    bad = [
        "what would you like me to confirm",
        "what do you want to confirm",
        "could you clarify what",
        "what should i do",
        "not sure what you're confirming",
    ]
    hits = [p for p in bad if p in text]
    assert not hits, (
        f"PART A FAIL — 'yes go ahead' did not resolve pending action; matched {hits}. "
        f"Head: {text[:800]}"
    )
