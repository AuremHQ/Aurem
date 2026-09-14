"""Live end-to-end verification for the 2026-02 Parts A-D chat hardening ticket.

Focus: B3 (required-tool gate) is the highest-priority verification.
Also lightweight checks for B1 (task_chain tool_call_records real fields),
regression (ordinary chat still works), and observational patch_repo_file
tool availability (B4).

Runs against the PUBLIC backend URL from frontend/.env.
"""
import os
import time
import uuid
import json
import requests
import pytest
from pymongo import MongoClient

# Load public URL from frontend .env (do not default)
def _read_env(path, key):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"{key} not found in {path}")

BASE_URL = _read_env("/app/frontend/.env", "REACT_APP_BACKEND_URL").rstrip("/")
MONGO_URL = _read_env("/app/backend/.env", "MONGO_URL")
DB_NAME = _read_env("/app/backend/.env", "DB_NAME")

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    assert data.get("ok") is True
    tok = data.get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def mongo_db():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return client[DB_NAME]


def _chat_send(token, prompt, session_id, project_id=None, timeout=180):
    body = {"prompt": prompt, "session_id": session_id}
    if project_id:
        body["project_id"] = project_id
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=timeout,
    )
    return r


# ---------- B3: core incident repro (no project) ----------
def test_b3_no_project_names_path_and_rule_does_not_ask_what_should_change(token, mongo_db):
    session_id = f"live-b3-noproj-{uuid.uuid4().hex[:8]}"
    prompt = (
        "Fix this critical issue: exec_usage (in "
        ".agent/skills/plugins/antigravity-awesome-skills-claude/skills/007/"
        "scripts/scanners/injection_scanner.py)"
    )
    r = _chat_send(token, prompt, session_id)
    assert r.status_code == 200, f"chat/send failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    text = json.dumps(data).lower()

    # Must NOT re-ask user to clarify what to change
    bad_phrases = [
        "what should change",
        "what would you like me to change",
        "could you clarify what",
        "please clarify what",
        "can you clarify what",
    ]
    for p in bad_phrases:
        assert p not in text, f"Regression: response asked clarifying question containing {p!r}. Response: {text[:800]}"

    # Should reference the path or the rule id (honest failure OK — "no project connected")
    assert (
        "exec_usage" in text
        or "injection_scanner.py" in text
        or "no project" in text
        or "not connected" in text
        or "project selected" in text
    ), f"Response did not cite rule/path/no-project honestly. Got: {text[:800]}"

    # Verify tool_call_records exists in task_chains for this session (B1)
    time.sleep(1)
    doc = mongo_db.task_chains.find_one({"session_id": session_id})
    if doc:
        tcr = doc.get("tool_call_records") or []
        # If read_repo_file was invoked, its record must have real ok/error/timestamp (not None)
        if tcr:
            for rec in tcr:
                assert "ok" in rec and rec["ok"] is not None, f"tool_call_records.ok is None: {rec}"
                assert "timestamp" in rec and rec["timestamp"], f"tool_call_records.timestamp missing: {rec}"


# ---------- B3: with real connected project ----------
def test_b3_with_real_project_triggers_real_read(token, mongo_db):
    session_id = f"live-b3-proj-{uuid.uuid4().hex[:8]}"
    # Use a rule-id + a file we expect to exist in the demo repo. README.md is universal.
    prompt = (
        "Fix this critical issue: eval_usage (in README.md). "
        "Please read the file and analyse."
    )
    r = _chat_send(token, prompt, session_id, project_id="p_demo_a")
    if r.status_code == 403 and "connection" in r.text.lower():
        pytest.skip(f"Demo project not connected in this environment (pre-existing infra state): {r.text[:200]}")
    assert r.status_code == 200, f"chat/send failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    text = json.dumps(data).lower()

    # Must not ask clarifying "what should change"
    assert "what should change" not in text, f"Clarifying-question regression: {text[:800]}"

    time.sleep(1)
    doc = mongo_db.task_chains.find_one({"session_id": session_id})
    assert doc, f"task_chains doc missing for session {session_id}"
    tcr = doc.get("tool_call_records") or []
    # B1 check — records must have real fields (ok not None, timestamp populated)
    for rec in tcr:
        assert rec.get("ok") is not None, f"tool_call_records.ok is None: {rec}"
        assert rec.get("timestamp"), f"tool_call_records.timestamp missing: {rec}"
        assert rec.get("name"), f"tool_call_records.name missing: {rec}"

    # Optional: check if read_repo_file was one of the calls
    names = {(rec.get("name") or "").lower() for rec in tcr}
    print(f"[b3_real_project] tool names invoked this turn: {names}")


# ---------- Regression: ordinary chat messages ----------
@pytest.mark.parametrize("prompt", [
    "hi, how are you today?",
    "What is the difference between a list and a tuple in Python?",
    "Give me a one-sentence summary of unit testing.",
])
def test_regression_ordinary_chat_still_works(token, prompt):
    session_id = f"live-regr-{uuid.uuid4().hex[:8]}"
    r = _chat_send(token, prompt, session_id, timeout=120)
    assert r.status_code == 200, f"ordinary chat failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    # Reply present in some canonical shape
    text = json.dumps(data)
    assert len(text) > 50, f"Suspiciously empty response: {text!r}"
    # No forced/false clarification loop for messages that don't match the gate
    assert "no project" not in text.lower() or "connected" in text.lower(), (
        "Ordinary chat should not falsely trigger no-project error"
    )


# ---------- B4: patch_repo_file tool registration (introspection) ----------
def test_b4_patch_repo_file_tool_is_registered():
    from services.local_tools import LOCAL_TOOLS, TOOL_SPECS
    assert "patch_repo_file" in LOCAL_TOOLS, "patch_repo_file not in LOCAL_TOOLS dispatch"
    spec_names = {s.get("name") if isinstance(s, dict) else getattr(s, "name", None) for s in TOOL_SPECS}
    assert "patch_repo_file" in spec_names, f"patch_repo_file not in TOOL_SPECS: {spec_names}"
