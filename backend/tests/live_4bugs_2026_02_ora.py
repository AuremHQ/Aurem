"""Live regression harness for 2026-02 four-bug round on AUREM /ora chat.

Covers:
- Bug 1: fresh stray-code-block-near-handoff live retries (stochastic, may not repro)
- Bug 2: business_voice filter live check (no '.that page'/'.that file' orphaning; quoted code intact)
- Bug 3: internal directive not leaked on file-not-found path

Run: python -m pytest tests/live_4bugs_2026_02_ora.py -v -s --tb=short
"""
import os
import re
import time
import json
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
PROJECT_ID = "p_64987820ec"

TIMEOUT = 180  # LLM retries can be slow


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/aurem-dev/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Login failed {r.status_code}: {r.text[:200]}")
    tok = r.json().get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


def _send(sess, prompt, session_id=None):
    payload = {"project_id": PROJECT_ID, "prompt": prompt}
    if session_id:
        payload["session_id"] = session_id
    r = sess.post(f"{BASE_URL}/api/aurem-dev/chat/send", json=payload, timeout=TIMEOUT)
    return r


def _extract_text(resp_json):
    # try common shapes
    for k in ("reply", "response", "message", "content", "text", "assistant_message"):
        v = resp_json.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            for kk in ("content", "text", "message"):
                if isinstance(v.get(kk), str):
                    return v[kk]
    # look for messages list
    msgs = resp_json.get("messages") or resp_json.get("history") or []
    if isinstance(msgs, list) and msgs:
        last = msgs[-1]
        if isinstance(last, dict):
            for kk in ("content", "text", "message"):
                if isinstance(last.get(kk), str):
                    return last[kk]
    return json.dumps(resp_json)[:4000]


# ---- Bug 1 live retries (stray code block near handoff) ----

@pytest.mark.parametrize("prompt", [
    "add a comment // qa test to frontend/src/index.js",
    "please add a small comment to frontend/src/index.js at the top",
    "add a comment to package.json",
    "add a harmless // qa comment to a valid file like frontend/src/index.js",
])
def test_bug1_live_stray_code_block_not_shown(session, prompt):
    r = _send(session, prompt)
    print(f"\n[Bug1] status={r.status_code} prompt={prompt!r}")
    if r.status_code != 200:
        pytest.skip(f"non-200 {r.status_code}: {r.text[:300]}")
    text = _extract_text(r.json())
    print(f"[Bug1] reply len={len(text)} head={text[:300]!r}")

    # Find aurem-handoff fence
    handoff_matches = re.findall(r"```aurem-handoff[\s\S]*?```", text)
    non_handoff_fences = re.findall(r"```(?!aurem-handoff)[\w-]*\n[\s\S]*?```", text)
    print(f"[Bug1] handoff_fences={len(handoff_matches)} stray_fences={len(non_handoff_fences)}")

    if handoff_matches:
        # If handoff exists, must NOT have any stray code fence around it
        assert not non_handoff_fences, (
            f"STRAY CODE BLOCK LEAKED alongside handoff fence. "
            f"stray={non_handoff_fences[0][:300]!r}"
        )
    # else: model didn't emit handoff at all -> can't reproduce, OK (stochastic)


# ---- Bug 2 live: no '.that page'/'.that file' orphaning; code intact ----

def test_bug2_live_no_leading_dot_orphaning_and_code_intact(session):
    r = _send(session, "Check sql_injection in validators.py line 88")
    print(f"\n[Bug2] status={r.status_code}")
    assert r.status_code == 200, r.text[:400]
    text = _extract_text(r.json())
    print(f"[Bug2] reply len={len(text)} sample={text[:500]!r}")

    assert ".that page" not in text, "Leading-dot orphaning bug returned: '.that page'"
    assert ".that file" not in text, "Leading-dot orphaning bug returned: '.that file'"

    # Also, if any fenced code appears, do a lightweight syntactic sanity check
    for m in re.finditer(r"```(?:python|py)?\n([\s\S]*?)```", text):
        code = m.group(1)
        # smoke-check: no words like 'a project page' or 'that file' in middle of code lines
        for bad in ("a project page", "that file", "that page"):
            assert bad not in code, f"Business-voice filter mangled code block: {bad!r} inside code fence: {code[:200]!r}"


# ---- Bug 3 live: file-not-found must NOT leak internal directive ----

def test_bug3_live_no_internal_directive_leak_on_missing_path(session):
    prompt = "Check exec_usage in some/made/up/path/injection_scanner.py line 194"
    r = _send(session, prompt)
    print(f"\n[Bug3] status={r.status_code}")
    assert r.status_code == 200, r.text[:400]
    text = _extract_text(r.json())
    print(f"[Bug3] reply len={len(text)} head={text[:500]!r}")

    banned = ["STOP guessing paths", "list_repo_files", "Your next tool call MUST"]
    for phrase in banned:
        assert phrase not in text, f"Internal directive leaked to user: {phrase!r}. Reply: {text[:600]!r}"


# ---- Bonus observation: 'a project page' duplication (5th bug, do NOT fail, just log) ----

def test_bonus_note_project_page_duplication(session):
    r = _send(session, "Summarise validators.py and injection_scanner.py in one line each")
    if r.status_code != 200:
        pytest.skip(f"non-200 {r.status_code}")
    text = _extract_text(r.json())
    dup = text.count("a project page")
    doubled_word = re.search(r"\b(\w+)\s+\1\b", text)
    print(f"\n[Bonus/Bug5-observation] 'a project page' count={dup} doubled_word={doubled_word.group(0) if doubled_word else None}")
    # do NOT assert; just record
