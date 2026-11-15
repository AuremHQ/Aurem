"""
routers/harness_telemetry.py — Aurem Loop harness telemetry (Fix E).

`POST /harness/telemetry` — the harness client already POSTs batches
here (aurem_loop/telemetry.py `_telemetry_send_batch`) and gets 404
today. Mounted with a bare prefix (no internal APIRouter(prefix=...))
under `/api` — same as routers/harness.py's verify endpoint, because
this is an external client contract, not a website route: the client
calls https://auremcto.com/api/harness/telemetry, with no
/aurem-dev segment.

Request: {"bin": str, "token": str, "harness_version": str,
"events": [{...whitelisted fields...}]}.
Response 200: {"received": int} = events ACCEPTED from the START of
the batch. The client requires an int > 0 to clear anything and
clamps to the batch size; it deletes the FIRST N events of the batch.
So acceptance is a PREFIX — validation stops at the first invalid
event and `received` is the length of the valid prefix. Event-level
problems return 200 {"received": 0} (nothing stored); only
envelope/batch-level problems return 422/413/401/429.

Auth is exactly like verify (services.harness_auth.
check_harness_token): bin must equal the user_id the token hash
belongs to. Invalid/unknown/revoked -> 401, NO event stored.
Any valid identity (free or paid) may send. verify is untouched.

Rate limits: 60/min per IP (before auth — flood control), then
10/min per authenticated user (same as verify). Over-limit -> 429.

Errors are generic: no stack traces, no echo of submitted values.
Never stored: the token, the raw body, IP fingerprints, or any
code/prompt/file-name content (those fields are not whitelisted).
No admin read endpoint — out of scope for this task.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from cto_services.db import require_db
from services.harness_auth import check_harness_token
from services.harness_telemetry import (
    MAX_BODY_BYTES,
    MAX_EVENTS_PER_BATCH,
    store_events,
    validate_batch,
)
from services.rate_limiter import check_rate_limit_async, client_ip_from_request

logger = logging.getLogger("aurem-dev.harness_telemetry")

router = APIRouter()

_IP_RATE_LIMIT_PER_MINUTE = 60
_USER_RATE_LIMIT_PER_MINUTE = 10  # same as verify
_HARNESS_VERSION_MAX_LEN = 64


@router.post("/harness/telemetry")
async def harness_telemetry(request: Request) -> dict:
    # Size gate BEFORE reading the body: a client that declares more
    # than the cap is rejected without buffering the bytes. (A client
    # that lies about Content-Length is still caught by the len(raw)
    # check below — the declared length is never trusted on its own.)
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise HTTPException(413, "Request body too large")
        except ValueError:
            raise HTTPException(422, "Invalid request body")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "Request body too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid request body")
    if not isinstance(data, dict):
        raise HTTPException(422, "Invalid request body")

    bin_id = data.get("bin")
    token = data.get("token")
    harness_version = data.get("harness_version")
    events = data.get("events")
    # Envelope shape: exact types, no coercion — a dict/list where a
    # string belongs (e.g. {"$ne": 1}) is rejected here, before auth.
    if (not isinstance(bin_id, str) or not bin_id.strip()
            or not isinstance(token, str) or not token.strip()
            or not isinstance(harness_version, str)
            or not harness_version.strip()
            or len(harness_version) > _HARNESS_VERSION_MAX_LEN
            or not isinstance(events, list)):
        raise HTTPException(422, "Invalid request body")
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise HTTPException(422, "Too many events in one batch")

    client_ip = client_ip_from_request(request)
    if not await check_rate_limit_async(
        f"harness_telemetry_ip:{client_ip}", _IP_RATE_LIMIT_PER_MINUTE,
    ):
        raise HTTPException(429, "Too many requests from this network. Try again in a minute.")

    db = require_db()
    if not await check_harness_token(db, bin=bin_id, token=token):
        raise HTTPException(401, "Invalid harness identity")

    if not await check_rate_limit_async(
        f"harness_telemetry:{bin_id}", _USER_RATE_LIMIT_PER_MINUTE,
    ):
        raise HTTPException(429, "Too many telemetry requests, try again shortly")

    accepted = validate_batch(events, user_id=bin_id)
    await store_events(db, user_id=bin_id, events=accepted)
    return {"received": len(accepted)}
