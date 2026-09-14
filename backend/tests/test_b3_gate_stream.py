"""B3 gate re-verification: /api/aurem-dev/chat/stream (SSE) must enforce required_read_gate.

Round 3 test - verifies fix where enforce_required_read_gate() moved from worker.py
to stream.py as the true last content-mutating step.
"""
import json
import os
import uuid
import time
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"

PROMPT = (
    "Fix this critical issue: exec_usage "
    "(in .agent/skills/plugins/antigravity-awesome-skills-claude/skills/007/scripts/scanners/injection_scanner.py)"
)
FILE_PATH_FRAGMENT = "injection_scanner.py"
RULE_ID = "exec_usage"


def login():
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("ok") is True
    assert "token" in data and data["token"]
    return data["token"]


def read_sse_stream(prompt: str, token: str, session_id: str, timeout: int = 90):
    """Return (concatenated_tokens_text, final_done_event_dict, raw_lines)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {"prompt": prompt, "session_id": session_id, "project_id": None}
    tokens = []
    done_event = None
    raw_lines = []
    with requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/stream",
        json=body,
        headers=headers,
        stream=True,
        timeout=timeout,
    ) as r:
        assert r.status_code == 200, f"stream returned {r.status_code}: {r.text[:500]}"
        start = time.time()
        for raw in r.iter_lines(decode_unicode=True):
            if time.time() - start > timeout:
                break
            if not raw:
                continue
            raw_lines.append(raw)
            if not raw.startswith("data:"):
                continue
            payload = raw[5:].strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if "token" in obj:
                tokens.append(obj["token"])
            if "delta" in obj:
                tokens.append(obj["delta"])
            # Final done event has provider/session_id and lacks 'type' (step events have type)
            if obj.get("done") is True and "type" not in obj:
                done_event = obj
                break
    joined = "".join(tokens)
    # Fallback: some code paths deliver the whole content in the done event
    if done_event:
        for k in ("text", "content", "final", "message"):
            v = done_event.get(k)
            if isinstance(v, str) and v and v not in joined:
                joined = joined + v if joined else v
                break
    return joined, done_event, raw_lines


# ---------- B3 GATE: chat/stream ----------

def test_stream_b3_gate_run_1():
    token = login()
    sid = f"test_b3_stream_{uuid.uuid4().hex[:10]}"
    text, done, raw = read_sse_stream(PROMPT, token, sid)
    print(f"\n[RUN 1 sid={sid}] final_text_len={len(text)}")
    print(f"[RUN 1] final_text (first 600 chars): {text[:600]}")
    if done:
        print(f"[RUN 1] done_event keys: {list(done.keys())}")
    assert done is not None, f"No done event received. Raw last lines: {raw[-10:]}"
    lower = text.lower()
    assert RULE_ID in lower, f"missing rule_id '{RULE_ID}' in stream text. Text: {text[:800]}"
    assert FILE_PATH_FRAGMENT in text, f"missing file path '{FILE_PATH_FRAGMENT}' in stream text. Text: {text[:800]}"
    # Should NOT be a generic clarifying question
    generic_flags = ["could you clarify", "can you clarify", "which file", "what file", "please provide more"]
    for f in generic_flags:
        assert f not in lower, f"Response is a generic clarifying question ('{f}'): {text[:600]}"


def test_stream_b3_gate_run_2():
    token = login()
    sid = f"test_b3_stream_{uuid.uuid4().hex[:10]}"
    text, done, raw = read_sse_stream(PROMPT, token, sid)
    print(f"\n[RUN 2 sid={sid}] final_text (first 600 chars): {text[:600]}")
    assert done is not None
    lower = text.lower()
    assert RULE_ID in lower, f"missing '{RULE_ID}': {text[:600]}"
    assert FILE_PATH_FRAGMENT in text, f"missing '{FILE_PATH_FRAGMENT}': {text[:600]}"


def test_stream_b3_gate_run_3():
    token = login()
    sid = f"test_b3_stream_{uuid.uuid4().hex[:10]}"
    text, done, raw = read_sse_stream(PROMPT, token, sid)
    print(f"\n[RUN 3 sid={sid}] final_text (first 600 chars): {text[:600]}")
    assert done is not None
    lower = text.lower()
    assert RULE_ID in lower, f"missing '{RULE_ID}': {text[:600]}"
    assert FILE_PATH_FRAGMENT in text, f"missing '{FILE_PATH_FRAGMENT}': {text[:600]}"


# ---------- chat/send regression ----------

def test_send_b3_gate_regression():
    token = login()
    sid = f"test_b3_send_{uuid.uuid4().hex[:10]}"
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/send",
        json={"prompt": PROMPT, "session_id": sid, "project_id": None},
        headers={"Authorization": f"Bearer {token}"},
        timeout=90,
    )
    assert r.status_code == 200, f"send status={r.status_code} body={r.text[:400]}"
    data = r.json()
    # response text may be under 'response', 'text', or 'message'
    text = data.get("response") or data.get("text") or data.get("message") or json.dumps(data)
    print(f"\n[SEND regression sid={sid}] text (first 600): {text[:600]}")
    lower = text.lower()
    assert RULE_ID in lower, f"send missing '{RULE_ID}': {text[:600]}"
    assert FILE_PATH_FRAGMENT in text, f"send missing '{FILE_PATH_FRAGMENT}': {text[:600]}"


# ---------- General chat/stream regression ----------

def test_stream_ordinary_message_completes():
    token = login()
    sid = f"test_ord_stream_{uuid.uuid4().hex[:10]}"
    text, done, raw = read_sse_stream("build me a login page", token, sid, timeout=90)
    print(f"\n[ORDINARY sid={sid}] text_len={len(text)} first_200={text[:200]}")
    assert done is not None, f"Ordinary stream did not complete with done event. Raw last: {raw[-5:]}"
    assert len(text) > 0, "Ordinary stream produced no tokens"
