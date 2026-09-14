"""Part B live repro against real running backend on port 8001 via ingress URL.

Sends two /chat/stream turns to project p_r10_testbed_e2e and checks
project_brains.updated_at between them.
"""
import json
import os
import sys
import time

import requests

BASE = "https://bin-context-pat.preview.emergentagent.com"
PROJECT_ID = "p_r10_testbed_e2e"


def login():
    r = requests.post(f"{BASE}/api/aurem-dev/auth/login",
                      json={"email": "test@aurem.dev", "password": "AuremTest2026!"},
                      timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def get_brain(tok):
    r = requests.get(f"{BASE}/api/aurem-dev/admin/project-brain/{PROJECT_ID}",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    print(f"[brain] status={r.status_code}")
    try:
        return r.json()
    except Exception:
        print(r.text[:300])
        return None


def send_stream(tok, prompt, session_id):
    print(f"[stream] sending: {prompt[:80]!r} session={session_id}")
    with requests.post(
        f"{BASE}/api/aurem-dev/chat/stream",
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        json={"prompt": prompt, "project_id": PROJECT_ID, "session_id": session_id},
        stream=True, timeout=120,
    ) as r:
        print(f"[stream] http status={r.status_code}")
        seen_done = False
        n = 0
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            n += 1
            if line.startswith("data:") and "\"done\"" in line:
                seen_done = True
            if n > 5000:
                break
        print(f"[stream] events={n} done={seen_done}")


def main():
    tok = login()
    print("Logged in.")

    b0 = get_brain(tok) or {}
    ts0 = (b0.get("brain") or {}).get("updated_at") or b0.get("updated_at")
    print(f"[T0] updated_at = {ts0}")

    # Turn 1: NO keyword match
    session1 = f"qa-indep-partb-nomatch-{int(time.time())}"
    send_stream(tok, "what does the get_status function in api.py return", session1)
    time.sleep(3)

    b1 = get_brain(tok) or {}
    ts1 = (b1.get("brain") or {}).get("updated_at") or b1.get("updated_at")
    print(f"[T1 after no-keyword] updated_at = {ts1}")
    print(f"UNCHANGED_AFTER_NO_KEYWORD = {ts0 == ts1}")

    # Turn 2: WITH decision phrase (Redis + Django — independently different from main agent's MongoDB+React repro)
    session2 = f"qa-indep-partb-decision-{int(time.time())}"
    send_stream(tok,
                "we decided to use Redis and Django for this project going forward",
                session2)
    time.sleep(3)

    b2 = get_brain(tok) or {}
    brain2 = b2.get("brain") or b2
    ts2 = brain2.get("updated_at")
    stack = brain2.get("tech_stack") or []
    decisions = brain2.get("decisions") or []
    print(f"[T2 after decision] updated_at = {ts2}")
    print(f"tech_stack (top): {stack[:10]}")
    print(f"decisions_count: {len(decisions)}")
    if decisions:
        print(f"newest decision title: {decisions[-1].get('title','')[:100]}")

    changed = ts1 != ts2
    has_redis = any("redis" in str(s).lower() for s in stack)
    has_django = any("django" in str(s).lower() for s in stack)
    has_decision_row = any("redis" in (d.get("title","").lower()) or
                           "django" in (d.get("title","").lower())
                           for d in decisions)
    print(f"CHANGED_AFTER_DECISION = {changed}")
    print(f"HAS_REDIS = {has_redis}  HAS_DJANGO = {has_django}  HAS_NEW_DECISION_ROW = {has_decision_row}")


main()
