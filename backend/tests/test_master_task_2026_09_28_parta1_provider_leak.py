"""
test_master_task_2026_09_28_parta1_provider_leak.py — MASTER TASK Part A1

Standing privacy rule: NO regular-user-facing chat response (non-streaming
/chat/send, streaming /chat/stream SSE frames, or GET /chat/history replay)
may ever surface an internal LLM vendor/model codename or a real dollar
cost. See services/user_response_sanitizer.py — the single choke point
these endpoints route `provider` through before it reaches the client.

This test is intentionally a blunt, deep scan (every string value in the
whole response body/every SSE frame, not just the known `provider` key)
so a FUTURE field added to any of these payloads that leaks a vendor name
fails this test immediately, without needing to know the field's name in
advance.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import requests

API = os.environ.get("REACT_APP_BACKEND_URL", "")
if not API:
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", ".env")) as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                API = line.strip().split("=", 1)[1]
API = f"{API}/api/aurem-dev"

# Vendor/provider/model codenames that must NEVER reach a regular user.
# Deliberately broad substrings (case-insensitive) — "mock" and "ora"
# are excluded on purpose (the sanitizer's own safe output + local-mode
# placeholder text both legitimately say "ORA"/"mock mode").
_FORBIDDEN_SUBSTRINGS = (
    "deepseek", "claude", "anthropic", "openrouter", "groq",
    "glm-5", "glm5", "z-ai", "gpt-4", "gpt-5", "longcat", "gemini",
)
_FORBIDDEN_KEYS = ("cost", "cost_usd", "model")


def _scan(obj, path="$"):
    """Recursively yields (path, offending_substring) for every forbidden
    key or forbidden substring found anywhere in `obj`."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _FORBIDDEN_KEYS:
                yield f"{path}.{k}", f"forbidden key {k!r}"
            yield from _scan(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _scan(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        low = obj.lower()
        for bad in _FORBIDDEN_SUBSTRINGS:
            if bad in low:
                yield path, bad


@pytest.fixture(scope="module")
def user():
    email = f"parta1-leak-{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": "ProviderLeak2026!", "name": "Leak Test",
    }, timeout=15)
    assert r.status_code == 200, r.text
    return {"email": email, "token": r.json()["token"]}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_chat_send_never_leaks_provider_model_or_cost(user):
    r = requests.post(
        f"{API}/chat/send",
        json={"prompt": "Say hello in one short sentence.", "session_id": f"leak-{uuid.uuid4().hex[:8]}"},
        headers=_auth(user["token"]), timeout=90,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("provider") in ("ORA", "", None), body.get("provider")
    leaks = list(_scan(body))
    assert leaks == [], f"leaked vendor/cost data in /chat/send: {leaks}"


def test_chat_stream_sse_never_leaks_provider_model_or_cost(user):
    session_id = f"leak-stream-{uuid.uuid4().hex[:8]}"
    with requests.post(
        f"{API}/chat/stream",
        json={"prompt": "Say hello in one short sentence.", "session_id": session_id},
        headers=_auth(user["token"]), timeout=90, stream=True,
    ) as r:
        assert r.status_code == 200, r.text
        frames = []
        for raw_line in r.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            try:
                frame = json.loads(raw_line[len("data: "):])
            except json.JSONDecodeError:
                continue
            frames.append(frame)
            if frame.get("done"):
                break
    assert frames, "no SSE frames received from /chat/stream"
    all_leaks = []
    for frame in frames:
        prov = frame.get("provider")
        if prov is not None:
            assert prov in ("ORA", ""), f"raw provider leaked in SSE frame: {prov!r}"
        prov_meta = frame.get("llm_provenance")
        if isinstance(prov_meta, dict):
            assert set(prov_meta.keys()) <= {"is_emergency"}, prov_meta
        all_leaks.extend(_scan(frame))
    assert all_leaks == [], f"leaked vendor/cost data in /chat/stream SSE: {all_leaks}"


def test_chat_history_replay_never_leaks_provider(user):
    session_id = f"leak-hist-{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{API}/chat/send",
        json={"prompt": "One word: hi.", "session_id": session_id},
        headers=_auth(user["token"]), timeout=90,
    )
    assert r.status_code == 200, r.text

    r2 = requests.get(
        f"{API}/chat/history", params={"session_id": session_id},
        headers=_auth(user["token"]), timeout=15,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assistant_turns = [m for m in body.get("messages", []) if m.get("role") == "assistant"]
    assert assistant_turns, "no assistant turn persisted for history replay check"
    for t in assistant_turns:
        assert t.get("provider") in ("ORA", "", None), t.get("provider")
    leaks = list(_scan(body))
    assert leaks == [], f"leaked vendor/cost data in GET /chat/history: {leaks}"
