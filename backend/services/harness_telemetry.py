"""
services/harness_telemetry.py — Aurem Loop harness telemetry (Fix E).

Server side of `POST /api/harness/telemetry`. The harness client
already POSTs batches here and gets 404 today; events stay queued
(cap 500) and retry forever.

Field list and types are derived from the client's TELEMETRY_FIELDS
(aurem_loop/constants.py:583-597) — the union of the per-type
whitelists, split back into per-type specs here so a field that
belongs to another event type is dropped, not stored:

  run:          type, bin, harness_version, model, task_type, passed
                (bool), attempts (int), seconds (float), error_category, at
  loop_c_run:   run + trigger
  version_published: type, bin, harness_version, model, version (int),
                weights_changed_pct (float), gate_gain (float),
                canary (bool), at

`at` is the client's string timestamp "%Y-%m-%dT%H:%M:%SZ".
`seconds` / `weights_changed_pct` / `gate_gain` accept int or float
(JSON 5 == 5.0); `attempts` / `version` require a real int.
bool is NEVER accepted where int/float is expected, and never the
other way round (Python's bool is a subclass of int — checked first).

Acceptance is a PREFIX: validate_batch walks the batch in order and
stops at the first invalid event, returning the valid prefix. That
is what the contract means by "ACCEPTED from the START of the batch"
and what the client assumes when it deletes the FIRST N events of
the batch. Unknown fields are DROPPED, never stored; a dict/list
where a scalar belongs is rejected outright (that is how a Mongo
operator injection like {"$ne": 1} is stopped — it can never reach
the query or the document).

Dedupe: the dedupe key is (user_id, event_hash), where event_hash is
the SHA-256 of the canonical JSON of the accepted fields + user_id.
`at` alone is NOT a safe key — it has 1-second resolution, and two
different runs in the same second would collide. Storage is an
upsert on that key, so a replayed batch returns received=N (every
event in it was accepted) but stores no second copy.

Retention: TTL index of 90 days on received_at — ASSUMPTION, the
founder confirms it (see the final report). Never stored: the token,
the raw body, IP-derived fingerprints, or any code/prompt/file-name
content (those fields are not whitelisted, so they cannot be stored).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone

logger = logging.getLogger("aurem-dev.harness_telemetry")

MAX_EVENTS_PER_BATCH = 50
MAX_BODY_BYTES = 65536  # 64 KiB
TELEMETRY_TTL_DAYS = 90
TELEMETRY_TTL_SECONDS = TELEMETRY_TTL_DAYS * 24 * 3600  # 7,776,000

_INT_MIN = 0
_INT_MAX = 1_000_000
_FLOAT_MAX_ABS = 1e15

# Per-type field specs: field name -> expected kind.
# "str" / "int" / "float" (int-or-float, not bool) / "bool".
TELEMETRY_EVENT_SPECS = {
    "run": {
        "type": "str", "bin": "str", "harness_version": "str",
        "model": "str", "task_type": "str", "passed": "bool",
        "attempts": "int", "seconds": "float", "error_category": "str",
        "at": "str",
    },
    "loop_c_run": {
        "type": "str", "bin": "str", "harness_version": "str",
        "model": "str", "task_type": "str", "passed": "bool",
        "attempts": "int", "seconds": "float", "error_category": "str",
        "trigger": "str", "at": "str",
    },
    "version_published": {
        "type": "str", "bin": "str", "harness_version": "str",
        "model": "str", "version": "int", "weights_changed_pct": "float",
        "gate_gain": "float", "canary": "bool", "at": "str",
    },
}

# String length caps per field (the client sends short values; a
# 100 KiB string in any field is junk, not telemetry).
TELEMETRY_STRING_MAX_LEN = {
    "type": 64, "bin": 256, "harness_version": 64, "model": 256,
    "task_type": 128, "error_category": 128, "trigger": 128, "at": 32,
}

_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _is_valid_str(value, field) -> bool:
    if not isinstance(value, str):
        return False
    if not value.strip():
        return False
    if len(value) > TELEMETRY_STRING_MAX_LEN.get(field, 256):
        return False
    return True


def _is_valid_int(value) -> bool:
    # bool first: isinstance(True, int) is True in Python.
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return _INT_MIN <= value <= _INT_MAX


def _is_valid_float(value) -> bool:
    # int is fine for a float field (JSON 5 == 5.0); bool is not.
    # Negative is allowed here — gate_gain can be negative (a loss).
    # The seconds >= 0 rule lives in validate_event, per-field.
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) <= _FLOAT_MAX_ABS
    if not isinstance(value, float):
        return False
    if not math.isfinite(value):
        return False
    return abs(value) <= _FLOAT_MAX_ABS


def _is_valid_bool(value) -> bool:
    return isinstance(value, bool)


_VALIDATORS = {
    "str": lambda v, f: _is_valid_str(v, f),
    "int": lambda v, f: _is_valid_int(v),
    "float": lambda v, f: _is_valid_float(v),
    "bool": lambda v, f: _is_valid_bool(v),
}


def validate_event(event, *, user_id: str) -> dict | None:
    """Validate one event against its type's whitelist.

    Returns the cleaned event (whitelisted fields only, unknown fields
    dropped) or None when the event is invalid. Never raises for bad
    input — bad input is the normal case here."""
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None
    spec = TELEMETRY_EVENT_SPECS.get(event_type)
    if spec is None:
        return None
    clean = {}
    for field, kind in spec.items():
        value = event.get(field, None)
        if value is None and field not in event:
            return None  # required field missing
        if not _VALIDATORS[kind](value, field):
            return None
        # Per-field rule: seconds is a duration, never negative.
        # (gate_gain / weights_changed_pct may be negative — a loss.)
        if field == "seconds" and value < 0:
            return None
        clean[field] = value
    # `at` must look like the client's timestamp, not just any string.
    if not _AT_RE.match(clean["at"]):
        return None
    # The event's bin must be the authenticated user — a bin from
    # anyone else is not this user's telemetry.
    if clean["bin"] != user_id:
        return None
    return clean


def validate_batch(events, *, user_id: str) -> list:
    """Validate a batch in order; stop at the first invalid event and
    return the valid prefix (possibly empty). A non-list batch has no
    valid prefix."""
    if not isinstance(events, list):
        return []
    accepted = []
    for event in events:
        clean = validate_event(event, user_id=user_id)
        if clean is None:
            break
        accepted.append(clean)
    return accepted


def event_hash(user_id: str, event: dict) -> str:
    """Dedupe key material: SHA-256 of the canonical JSON of the
    accepted fields plus the user. Deterministic; two different runs
    (or two users) never share a hash. Contains no token."""
    canonical = json.dumps(
        {"user_id": user_id, **event},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def store_events(db, *, user_id: str, events: list) -> int:
    """Store each accepted event (one doc per event) in
    `harness_telemetry`, upserting on (user_id, event_hash) so a
    replayed batch stores no duplicate. Returns the number of events
    accepted (= len(events)) — a replay still counts as accepted, it
    just does not double-store. Never stores the token or the raw
    body; the doc is user_id + accepted fields + received_at."""
    for event in events:
        doc = {
            "user_id": user_id,
            **event,
            "received_at": datetime.now(timezone.utc),
            "event_hash": event_hash(user_id, event),
        }
        await db.harness_telemetry.update_one(
            {"user_id": user_id, "event_hash": doc["event_hash"]},
            {"$setOnInsert": doc},
            upsert=True,
        )
    return len(events)


async def ensure_indexes(db) -> None:
    """Create the telemetry indexes (idempotent): a 90-day TTL on
    received_at, and a unique index on (user_id, event_hash) that
    makes the dedupe atomic in real Mongo."""
    await db.harness_telemetry.create_index(
        "received_at",
        expireAfterSeconds=TELEMETRY_TTL_SECONDS,
        name="harness_telemetry_received_at_ttl",
    )
    await db.harness_telemetry.create_index(
        [("user_id", 1), ("event_hash", 1)],
        unique=True,
        name="harness_telemetry_user_event_unique",
    )
