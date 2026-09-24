"""Round 4 final confirmation for B3 required_read_gate.

Test targets:
  1. POST /api/aurem-dev/chat/stream (SSE) - 5 fresh sessions, must be 5/5.
  2. POST /api/aurem-dev/chat/send      - 3 fresh sessions, must be 3/3.
  3. Regression - ordinary chat via both endpoints.
  4. Trace-completeness check on Mongo task_chains.
"""
import json
import os
import re
import time
import uuid

import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "https://bin-context-pat.preview.emergentagent.com"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

CANONICAL_PATH = ".agent/skills/plugins/antigravity-awesome-skills-claude/skills/007/scripts/scanners/injection_scanner.py"
RULE_ID = "exec_usage"
PROMPT = f"Fix this critical issue: {RULE_ID} (in {CANONICAL_PATH})"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/aurem-dev/auth/login",
                      json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    assert tok, f"no token in {data}"
    return tok


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _parse_sse_tokens(text: str) -> str:
    """Concatenate all `data: {token: ...}` fragments from an SSE stream."""
    out = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        if isinstance(obj, dict):
            tok = obj.get("token")
            if isinstance(tok, str):
                out.append(tok)
            # some servers emit final "message" or "content"
            if not tok:
                for k in ("content", "message", "text", "delta"):
                    v = obj.get(k)
                    if isinstance(v, str):
                        out.append(v)
    return "".join(out)


def _stream_once(headers, session_id, prompt=PROMPT, timeout=180):
    url = f"{BASE_URL}/api/aurem-dev/chat/stream"
    body = {"prompt": prompt, "project_id": None, "session_id": session_id}
    with requests.post(url, json=body, headers=headers, stream=True, timeout=timeout) as r:
        assert r.status_code == 200, f"stream status {r.status_code}: {r.text[:400]}"
        raw = r.text  # requests will buffer; SSE will end when server closes
    return raw, _parse_sse_tokens(raw)


def _send_once(headers, session_id, prompt=PROMPT, timeout=180):
    url = f"{BASE_URL}/api/aurem-dev/chat/send"
    body = {"prompt": prompt, "project_id": None, "session_id": session_id}
    r = requests.post(url, json=body, headers=headers, timeout=timeout)
    assert r.status_code == 200, f"send status {r.status_code}: {r.text[:400]}"
    data = r.json()
    # extract reply text - try a few known keys
    for k in ("reply", "response", "message", "content", "text", "output"):
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, str) and v.strip():
            return data, v
    return data, json.dumps(data)


def _assert_canonical(reply_text: str, run_label: str):
    assert RULE_ID in reply_text, f"[{run_label}] rule_id '{RULE_ID}' MISSING in reply. Raw={reply_text!r}"
    assert CANONICAL_PATH in reply_text, (
        f"[{run_label}] canonical path MISSING (mangled?). "
        f"Looking for: {CANONICAL_PATH}\nFULL RAW REPLY:\n{reply_text}"
    )
    # check mangled marker didn't leak
    assert ".that page" not in reply_text, f"[{run_label}] mangled '.that page' still present. Raw={reply_text!r}"


# ---------- 1. stream 5x ----------
STREAM_RUNS = []


@pytest.mark.parametrize("run_idx", list(range(1, 6)))
def test_stream_gate_5x(headers, run_idx):
    session_id = f"round4-stream-{uuid.uuid4()}"
    raw, reply = _stream_once(headers, session_id)
    STREAM_RUNS.append({"run": run_idx, "session_id": session_id, "raw_len": len(raw), "reply": reply})
    try:
        _assert_canonical(reply, f"stream run {run_idx} sid={session_id}")
    except AssertionError:
        # dump raw for evidence
        out = f"/app/test_reports/round4_stream_run{run_idx}_FAIL.txt"
        with open(out, "w") as f:
            f.write(f"session_id={session_id}\n\n=== RAW SSE ===\n{raw}\n\n=== PARSED REPLY ===\n{reply}\n")
        raise


# ---------- 2. send 3x ----------
SEND_RUNS = []


@pytest.mark.parametrize("run_idx", list(range(1, 4)))
def test_send_gate_3x(headers, run_idx):
    session_id = f"round4-send-{uuid.uuid4()}"
    data, reply = _send_once(headers, session_id)
    SEND_RUNS.append({"run": run_idx, "session_id": session_id, "reply": reply, "raw_data": data})
    try:
        _assert_canonical(reply, f"send run {run_idx} sid={session_id}")
    except AssertionError:
        out = f"/app/test_reports/round4_send_run{run_idx}_FAIL.txt"
        with open(out, "w") as f:
            f.write(f"session_id={session_id}\n\n=== FULL JSON ===\n{json.dumps(data, indent=2, default=str)}\n\n=== REPLY ===\n{reply}\n")
        raise


# ---------- 3. regression: ordinary chat ----------
def test_regression_ordinary_send(headers):
    sid = f"round4-reg-send-{uuid.uuid4()}"
    data, reply = _send_once(headers, sid, prompt="what can you help me with")
    assert len(reply) > 20, f"reply too short: {reply!r}"
    assert ".that page" not in reply
    assert CANONICAL_PATH not in reply  # gate should NOT fire on unrelated msg
    # sanity: some english word
    assert re.search(r"[A-Za-z]{3,}", reply), f"no english words: {reply!r}"


def test_regression_ordinary_stream(headers):
    sid = f"round4-reg-stream-{uuid.uuid4()}"
    raw, reply = _stream_once(headers, sid, prompt="hello, briefly what can you help me with?")
    assert len(reply) > 10, f"stream reply too short: {reply!r}\nraw={raw[:500]}"
    assert ".that page" not in reply
    assert CANONICAL_PATH not in reply


# ---------- 4. mongo trace-completeness ----------
def test_mongo_trace_completeness():
    # pick the first successful stream run's session_id
    assert STREAM_RUNS, "no stream runs recorded - stream tests must run first"
    # find one whose reply contains canonical path (a passing one)
    candidate = None
    for r in STREAM_RUNS:
        if CANONICAL_PATH in r["reply"] and RULE_ID in r["reply"]:
            candidate = r
            break
    if candidate is None:
        pytest.skip("no passing stream run to inspect - upstream failure already reported")

    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]

    # allow a few seconds for background write
    doc = None
    for _ in range(10):
        doc = db.task_chains.find_one({"session_id": candidate["session_id"]})
        if doc:
            break
        time.sleep(1)

    assert doc is not None, f"no task_chains doc for session_id={candidate['session_id']}"
    tcr = doc.get("tool_call_records") or []
    assert isinstance(tcr, list), f"tool_call_records not a list: {type(tcr)}"
    assert len(tcr) > 0, f"tool_call_records empty for {candidate['session_id']}"

    # find read_repo_file entry with canonical path
    matches = []
    for entry in tcr:
        if not isinstance(entry, dict):
            continue
        if entry.get("tool") == "read_repo_file":
            args = entry.get("args") or {}
            args_str = json.dumps(args) if isinstance(args, (dict, list)) else str(args)
            if CANONICAL_PATH in args_str:
                matches.append(entry)
    assert matches, (
        f"No tool_call_records entry with tool=read_repo_file and canonical path.\n"
        f"session_id={candidate['session_id']}\n"
        f"tool_call_records={json.dumps(tcr, indent=2, default=str)[:3000]}"
    )
    # persist evidence
    with open("/app/test_reports/round4_mongo_trace_evidence.json", "w") as f:
        json.dump({"session_id": candidate["session_id"], "matching_entries": matches, "all_records_count": len(tcr)}, f, indent=2, default=str)
