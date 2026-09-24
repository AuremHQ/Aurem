"""
routers/evidence.py — 2026-09-20

Companion to services/evidence_pointer.py. A persisted chat message
(scan-result, Loop Mode end-of-run turn) carries a short preview + a
pointer (`kind` + `record_id`) — never the full record. This is the
ONE route that resolves that pointer back to the real stored
document, scoped to the requesting user so a pointer copied out of
one user's chat can't be used to read another user's scan / loop
data.

  GET /api/aurem-dev/evidence/{kind}/{record_id}
      kind: "scan" | "loop_run"
      Returns: { ok, kind, record_id, record: <full stored doc> }
"""
from __future__ import annotations

import logging
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Header

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.evidence_pointer import EVIDENCE_COLLECTIONS

router = APIRouter(prefix="/evidence", tags=["Evidence"])
logger = logging.getLogger(__name__)


@router.get("/{kind}/{record_id}")
async def get_evidence(
    kind: str, record_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    user_id = user["user_id"]
    collection = EVIDENCE_COLLECTIONS.get(kind)
    if not collection:
        raise HTTPException(404, f"unknown evidence kind: {kind}")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    query: dict = {"user_id": user_id}
    if kind == "scan":
        try:
            query["_id"] = ObjectId(record_id)
        except InvalidId:
            raise HTTPException(404, "record not found")
        doc = await db[collection].find_one(query)
        if not doc:
            raise HTTPException(404, "record not found")
        doc["_id"] = str(doc["_id"])
    else:  # loop_run — primary key is loop_id, not the Mongo _id
        query["loop_id"] = record_id
        doc = await db[collection].find_one(query, {"_id": 0})
        if not doc:
            raise HTTPException(404, "record not found")
    return {"ok": True, "kind": kind, "record_id": record_id, "record": doc}
