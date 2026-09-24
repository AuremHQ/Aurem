"""Retest B3 required-tool gate after 2026-02 fixes:
   1) intent-classifier tier=clarify skip when gate matches (chat_pre_llm.py)
   2) enforce_required_read_gate moved to LAST step in chat/turn.py (after bail_reason/output_guard/business_voice)
   3) chat/stream (worker.py) enforcement right after _run_orchestrator
   4) task_chains tool_call_records[].tool field name (not 'name')
"""
import os, time, uuid, json, re
import requests, pytest
from pymongo import MongoClient


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

INCIDENT_PROMPT = (
    "Fix this critical issue: exec_usage (in "
    ".agent/skills/plugins/antigravity-awesome-skills-claude/skills/007/"
    "scripts/scanners/injection_scanner.py)"
)

CLARIFY_PATTERNS = [
    r"what exactly should change",
    r"what should change",
    r"which file/part",
    r"which file or part",
    r"could you clarify",
    r"please clarify what",
    r"can you clarify what",
    r"what would you like me to change",
]


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/aurem-dev/auth/login",
                      json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:400]}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def mongo_db():
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _extract_reply_text(data):
    # Best-effort extraction of user-facing content from response JSON
    candidates = []
    for k in ("reply", "response", "message", "content", "text", "assistant"):
        v = data.get(k)
        if isinstance(v, str):
            candidates.append(v)
        elif isinstance(v, dict):
            for k2 in ("content", "text", "message"):
                if isinstance(v.get(k2), str):
                    candidates.append(v[k2])
    # Fallback: full serialization
    candidates.append(json.dumps(data))
    return "\n".join(candidates)


def _assert_no_clarify_and_cites(text):
    low = text.lower()
    for pat in CLARIFY_PATTERNS:
        assert not re.search(pat, low), f"Clarifying-question regression matched {pat!r}. Text: {low[:800]}"
    assert "exec_usage" in low, f"Response does not mention 'exec_usage'. Text: {low[:800]}"
    assert ("injection_scanner.py" in low or "injection_scanner" in low), \
        f"Response does not mention file path. Text: {low[:800]}"
    # honest failure signal (no project connected / could not read)
    ok = any(s in low for s in [
        "no project", "not connected", "project connected", "project selected",
        "could not read", "couldn't read", "unable to read", "read failed",
        "no repo", "no repository", "connect", "read_repo_file",
    ])
    assert ok, f"Response does not explain read failure. Text: {low[:800]}"


# ---------- B3 chat/send: 3 fresh session_ids ----------
@pytest.mark.parametrize("i", [1, 2, 3])
def test_b3_chat_send_incident_fresh_session(token, mongo_db, i):
    session_id = f"retest-b3-send-{i}-{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": INCIDENT_PROMPT, "session_id": session_id, "project_id": None},
        timeout=180,
    )
    assert r.status_code == 200, f"chat/send failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    text = _extract_reply_text(data)
    print(f"\n[run {i}] session={session_id}\nRESPONSE (first 600): {text[:600]}\n")
    _assert_no_clarify_and_cites(text)

    # task_chains field-name correction: check 'tool' field (not 'name')
    time.sleep(1.0)
    doc = mongo_db.task_chains.find_one({"session_id": session_id})
    if doc:
        tcr = doc.get("tool_call_records") or []
        print(f"[run {i}] tool_call_records count={len(tcr)}")
        for rec in tcr:
            print(f"[run {i}] rec keys={list(rec.keys())} tool={rec.get('tool')!r} name={rec.get('name')!r} ok={rec.get('ok')!r} ts={rec.get('timestamp')!r}")
        if tcr:
            # At least one record should have 'tool' populated (per codebase convention)
            tools = [rec.get("tool") for rec in tcr if rec.get("tool")]
            assert tools, f"No tool_call_records had 'tool' field populated. Records: {tcr}"
            for rec in tcr:
                assert rec.get("ok") is not None, f"ok=None in {rec}"
                assert rec.get("timestamp"), f"timestamp missing in {rec}"


# ---------- B3 chat/stream: SSE path ----------
def test_b3_chat_stream_incident(token, mongo_db):
    session_id = f"retest-b3-stream-{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        json={"prompt": INCIDENT_PROMPT, "session_id": session_id, "project_id": None},
        timeout=180, stream=True,
    )
    assert r.status_code == 200, f"chat/stream failed: {r.status_code} {r.text[:400]}"
    chunks = []
    started = time.time()
    for line in r.iter_lines(decode_unicode=True):
        if line:
            chunks.append(line)
        if time.time() - started > 120:
            break
    r.close()
    full = "\n".join(chunks)
    print(f"\n[stream] session={session_id}\nSSE (first 1200 chars): {full[:1200]}\n")
    assert full, "Empty SSE stream"
    _assert_no_clarify_and_cites(full)


# ---------- Regression: ordinary chat messages must not be affected ----------
@pytest.mark.parametrize("prompt", [
    "hi, how are you today?",
    "build me a login page",
    "what does app.py do",
    "What is the difference between a list and a tuple in Python?",
])
def test_regression_ordinary_chat(token, prompt):
    session_id = f"retest-regr-{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": prompt, "session_id": session_id, "project_id": None},
        timeout=120,
    )
    assert r.status_code == 200, f"ordinary chat failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    text = _extract_reply_text(data)
    low = text.lower()
    assert len(text) > 30, f"Suspiciously empty response: {text!r}"
    # Ordinary chat must NOT get the B3 gate's exec_usage/injection_scanner override
    assert "exec_usage" not in low, f"Ordinary chat wrongly triggered exec_usage: {low[:400]}"
    assert "injection_scanner" not in low, f"Ordinary chat wrongly triggered injection_scanner: {low[:400]}"
