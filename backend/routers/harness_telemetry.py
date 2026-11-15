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
Response 200: {"received": int} = events CONSUMED from the START of
the batch — valid ones stored, invalid ones dropped and counted.
The client requires an int > 0 to clear anything and clamps to the
batch size; it deletes the FIRST N events of the batch. So
consumption is a PREFIX: the server deals with the batch in order
and `received` is how many events from the start it dealt with.
An invalid event is permanent junk (re-sending it would never make
it valid), so it is dropped and counted instead of stopping the
batch — the old "received 0 on a bad first event" is the poison
pill: the client clears nothing and resends the same batch forever.
Event-level problems therefore return 200 with received = consumed
(the invalid events are never stored); only envelope/batch-level
problems return 422/413/401/429.

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


def _check_declared_size(request: Request) -> None:
    """Size gate BEFORE reading the body: a client that declares more
    than the cap is rejected without buffering the bytes. (A client
    that lies about Content-Length is still caught by the len(raw)
    check in the handler — the declared length is never trusted on
    its own.)"""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise HTTPException(413, "Request body too large")
        except ValueError:
            raise HTTPException(422, "Invalid request body")


def _parse_json_body(raw: bytes) -> dict:
    """Decode the body as a JSON object; anything else is a 422."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid request body")
    if not isinstance(data, dict):
        raise HTTPException(422, "Invalid request body")
    return data


def _validate_identity_fields(data: dict) -> tuple:
    """Envelope identity: exact types, no coercion — a dict/list where
    a string belongs (e.g. {"$ne": 1}) is rejected here, before auth.
    Returns (bin, token, harness_version)."""
    bin_id = data.get("bin")
    token = data.get("token")
    harness_version = data.get("harness_version")
    if (not isinstance(bin_id, str) or not bin_id.strip()
            or not isinstance(token, str) or not token.strip()
            or not isinstance(harness_version, str)
            or not harness_version.strip()
            or len(harness_version) > _HARNESS_VERSION_MAX_LEN):
        raise HTTPException(422, "Invalid request body")
    return bin_id, token, harness_version


def _validate_events_field(data: dict) -> list:
    """Envelope events: must be a list within the batch cap."""
    events = data.get("events")
    if not isinstance(events, list):
        raise HTTPException(422, "Invalid request body")
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise HTTPException(422, "Too many events in one batch")
    return events


def _validate_envelope(data: dict) -> tuple:
    """Envelope shape: identity fields + events list. Returns
    (bin, token, harness_version, events)."""
    bin_id, token, harness_version = _validate_identity_fields(data)
    events = _validate_events_field(data)
    return bin_id, token, harness_version, events


async def _enforce_ip_rate_limit(request: Request) -> str:
    """Per-IP flood control, before auth. Returns the client IP."""
    client_ip = client_ip_from_request(request)
    if not await check_rate_limit_async(
        f"harness_telemetry_ip:{client_ip}", _IP_RATE_LIMIT_PER_MINUTE,
    ):
        raise HTTPException(429, "Too many requests from this network. Try again in a minute.")
    return client_ip


async def _enforce_user_rate_limit(bin_id: str) -> None:
    """Per-authenticated-user limit, after auth (same as verify)."""
    if not await check_rate_limit_async(
        f"harness_telemetry:{bin_id}", _USER_RATE_LIMIT_PER_MINUTE,
    ):
        raise HTTPException(429, "Too many telemetry requests, try again shortly")


@router.post("/harness/telemetry")
async def harness_telemetry(request: Request) -> dict:
    _check_declared_size(request)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "Request body too large")
    data = _parse_json_body(raw)
    bin_id, token, _harness_version, events = _validate_envelope(data)
    await _enforce_ip_rate_limit(request)

    db = require_db()
    if not await check_harness_token(db, bin=bin_id, token=token):
        raise HTTPException(401, "Invalid harness identity")

    await _enforce_user_rate_limit(bin_id)

    accepted, consumed = validate_batch(events, user_id=bin_id)
    await store_events(db, user_id=bin_id, events=accepted)
    return {"received": consumed}
