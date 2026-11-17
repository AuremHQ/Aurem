"""
routers/admin_harness.py — Aurem Loop harness, Phase B admin surface.

Release management (create draft / publish / list) and the download
log view. Every write route is admin-gated via require_admin_dep
(cto_services.auth) — same pattern as every other admin router in this
codebase (see routers/admin_observability.py, routers/admin_bin.py).

Mass-assignment guard: HarnessReleaseCreate intentionally has no
`status` or `is_latest` field — create_draft_release() always produces
a draft; publishing is a separate, explicit POST to
/admin/harness/releases/{release_id}/publish. An attacker (or an
honest mistake) supplying extra "status"/"is_latest" keys in the
request body is silently ignored by Pydantic's own field set, never
reaching services.harness_releases.

D1 (hosting the real fix-branch package) is not approved — the
`source` field on every created release is one of a known, closed set
of values, never free text, so the provenance of what's hosted is
always visible in this admin view. "admin_ui" (2026-11-16) is a human
admin manually uploading a zip through this page — a real provenance
distinct from "test_fixture" (synthetic bytes in a test) and
"muse_fix_branch_approved" (the real fix-branch package, once D1 is
approved); it carries the same weight as the others for audit
purposes, not a bypass of the D1 gate.
"""
from __future__ import annotations

import base64
import binascii
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import require_admin_dep
from cto_services.db import require_db
from services.harness_releases import create_draft_release, publish_release

logger = logging.getLogger("aurem-dev.admin_harness")

router = APIRouter(dependencies=[Depends(require_admin_dep)])

_VALID_SOURCES = ("test_fixture", "muse_fix_branch_approved", "admin_ui")
_MAX_CHANGELOG_ITEMS = 50
_MAX_CHANGELOG_ITEM_LEN = 500


class HarnessReleaseCreate(BaseModel):
    version: str = Field(..., min_length=1, max_length=64)
    platform: str = Field("windows", max_length=32)
    changelog: list[str] = Field(default_factory=list, max_length=_MAX_CHANGELOG_ITEMS)
    mandatory: bool = False
    artifact_base64: str = Field(..., min_length=1)
    source: str = Field(...)

    def validated_changelog(self) -> list[str]:
        return self.changelog

    @property
    def artifact_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.artifact_base64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(400, "artifact_base64 is not valid base64") from e


@router.post("/admin/harness/releases")
async def admin_create_release(
    body: HarnessReleaseCreate,
    admin: dict = Depends(require_admin_dep),
) -> dict:
    if body.source not in _VALID_SOURCES:
        raise HTTPException(422, f"source must be one of {_VALID_SOURCES}")
    for item in body.changelog:
        if len(item) > _MAX_CHANGELOG_ITEM_LEN:
            raise HTTPException(422, "a changelog entry exceeds the length cap")

    db = require_db()
    try:
        result = await create_draft_release(
            db,
            version=body.version, platform=body.platform,
            changelog=body.changelog, mandatory=body.mandatory,
            artifact_bytes=body.artifact_bytes, source=body.source,
            created_by=admin.get("user_id", "unknown-admin"),
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return result


@router.post("/admin/harness/releases/{release_id}/publish")
async def admin_publish_release(release_id: str) -> dict:
    db = require_db()
    try:
        return await publish_release(db, release_id=release_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/admin/harness/releases")
async def admin_list_releases() -> dict:
    db = require_db()
    rows = await db.harness_releases.find(
        {}, {"_id": 0},
    ).sort("created_at", -1).limit(200).to_list(200)
    return {"releases": rows}


@router.get("/admin/harness/downloads")
async def admin_list_downloads(
    plan: str | None = None,
    version: str | None = None,
) -> dict:
    db = require_db()
    filt: dict = {}
    if plan:
        filt["plan"] = plan
    if version:
        filt["version"] = version
    rows = await db.harness_downloads.find(
        filt, {"_id": 0},
    ).sort("issued_at", -1).limit(500).to_list(500)
    total = len(rows)
    free_count = sum(1 for r in rows if r.get("plan") == "free")
    paid_count = total - free_count
    return {
        "downloads": rows, "total": total,
        "free_count": free_count, "paid_count": paid_count,
    }


# 2026-11-16 — admin Harness side-panel section. Signups + telemetry
# status, both real backend reads, never hardcoded. Downloads and
# releases are deliberately NOT duplicated here — the admin page
# calls the existing /admin/harness/downloads and links to the
# existing /admin/harness-releases page instead.
@router.get("/admin/harness/signups")
async def admin_list_harness_signups(source: str | None = None) -> dict:
    """Recent signups with their attribution `signup_source`, so the
    admin can tell harness_homepage-sourced accounts apart from every
    other signup path. `source` optionally filters to one value;
    omitted shows everything (most rows will have signup_source=None
    — every signup path that predates this field, or any path other
    than the harness page)."""
    db = require_db()
    filt: dict = {}
    if source:
        filt["signup_source"] = source
    rows = await db.dev_users.find(
        filt, {"_id": 0, "email": 1, "created_at": 1, "signup_source": 1},
    ).sort("created_at", -1).limit(200).to_list(200)
    harness_count = await db.dev_users.count_documents({"signup_source": "harness_homepage"})
    return {"signups": rows, "total": len(rows), "harness_homepage_count": harness_count}


@router.get("/admin/harness/telemetry-status")
async def admin_harness_telemetry_status() -> dict:
    """Real status of the telemetry pipeline (services/harness_telemetry.py
    + POST /api/harness/telemetry) — event count and most recent
    received_at, never a hardcoded "it works" claim."""
    db = require_db()
    total = await db.harness_telemetry.count_documents({})
    latest = await db.harness_telemetry.find_one(
        {}, {"_id": 0, "received_at": 1}, sort=[("received_at", -1)],
    )
    return {
        "endpoint": "/api/harness/telemetry",
        "total_events": total,
        "last_received_at": latest["received_at"].isoformat() if latest and latest.get("received_at") else None,
    }
