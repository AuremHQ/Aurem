"""
test_p0_3_github_token_leak_guard.py — 2026-09-27 P0-3 follow-up

Standing forensic + defense-in-depth guard: any future GitHub-token
-shaped value in a JSON response body under /api/aurem-dev/loop/*
must be redacted before it leaves the server, and logged as a
CRITICAL security event — independent of whether the specific
code-level fix in routers/loop.py (2026-09-26) stays in place.
"""
from __future__ import annotations

import asyncio
import json

from main import GithubTokenLeakGuardMiddleware

_FAKE_TOKEN = (
    "ghs_1234567_a1B2c3D4-e5F6_g7H8.i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4y5Z6"
    ".a7B8c9D0e1F2"
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _dispatch(downstream_body: dict, path: str, headers=None):
    async def fake_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                    "body": json.dumps(downstream_body).encode(),
                    "more_body": False})

    mw = GithubTokenLeakGuardMiddleware(fake_app)
    scope = {"type": "http", "path": path, "headers": headers or []}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)
    body = next(m for m in sent if m["type"] == "http.response.body")
    return body["body"].decode()


def test_leaked_token_in_loop_path_is_redacted():
    body = _run(_dispatch(
        {"ok": True, "context": {"ship_pending": {"token": _FAKE_TOKEN}}},
        "/api/aurem-dev/loop/some-loop-id/status",
    ))
    assert _FAKE_TOKEN not in body, "token must never reach the client"
    assert "[REDACTED_GITHUB_TOKEN]" in body
    parsed = json.loads(body)  # must still be valid JSON after redaction
    assert parsed["context"]["ship_pending"]["token"] == "[REDACTED_GITHUB_TOKEN]"


def test_normal_loop_response_passes_through_unchanged():
    body = _run(_dispatch(
        {"ok": True, "state": "RUNNING", "context": {}},
        "/api/aurem-dev/loop/some-loop-id/status",
    ))
    assert json.loads(body) == {"ok": True, "state": "RUNNING", "context": {}}


def test_non_loop_path_is_never_touched_even_with_a_token_shaped_string():
    """Scope is intentionally narrow — this guard only buffers /loop/*
    bodies. A token-shaped value elsewhere is NOT this middleware's
    job (that's output_guard/vanguard's job) and must pass through
    untouched, proving there's no unscoped body-buffering overhead
    on the rest of the API."""
    body = _run(_dispatch(
        {"note": _FAKE_TOKEN}, "/api/aurem-dev/usage/me",
    ))
    assert _FAKE_TOKEN in body
