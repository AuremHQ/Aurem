"""
routers/harness_downloads.py — Aurem Loop harness, Phase B download.

`POST /harness/download` — a logged-in website user (free or paid)
downloads the current published Windows release, with a freshly-
issued, per-installation identity.json baked into the zip. Mounted at
`/api/aurem-dev` (standard JWT-auth convention for this codebase) —
unlike routers/harness.py's verify endpoint, there is no pre-existing
external client contract pinning this path, since it's called by the
logged-in browser, not the harness binary.

`POST /harness/download-exe` — the same gate, but the Windows .exe
installer (AuremLoop-Setup-{version}.exe) instead of the zip. The
.exe is served byte-for-byte: no identity.json can be injected into
an .exe, so its delivered SHA-256 always matches the release's
`exe_sha256` exactly (unlike the zip, whose delivered SHA differs
from the release SHA because identity.json is baked in). The .exe is
NOT code-signed (no certificate exists) — Windows SmartScreen will
warn on first run; that is expected and must not be claimed otherwise.

Hybrid model: there is only ONE package per platform. Free vs paid is
never encoded by shipping different zip contents — it's enforced at
harness startup by the Phase A verify endpoint reading the live tier.
Re-downloading issues a new, independent token; it never revokes an
existing installation (see services.harness_auth.issue_harness_token).

No release published yet -> a clean 409, never a fabricated package.
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Response

from cto_services.auth import current_dev
from cto_services.db import require_db
from services.harness_auth import issue_harness_token
from services.harness_releases import (
    exe_filename_for_version,
    get_latest_release,
    get_release_artifact_bytes,
    get_release_exe_bytes,
    inject_identity_into_zip,
)
from services.rate_limiter import check_rate_limit_async

logger = logging.getLogger("aurem-dev.harness_downloads")

router = APIRouter()

_DOWNLOAD_RATE_LIMIT_PER_MINUTE = 5
_PLATFORM = "windows"  # v1 is Windows-only, per the plan's own scope


@router.post("/harness/download")
async def harness_download(authorization: str | None = Header(None)) -> Response:
    user = await current_dev(authorization)
    user_id = user["user_id"]

    allowed = await check_rate_limit_async(
        f"harness_download:{user_id}", _DOWNLOAD_RATE_LIMIT_PER_MINUTE,
    )
    if not allowed:
        raise HTTPException(429, "Too many download attempts, try again shortly")

    db = require_db()

    release = await get_latest_release(db, platform=_PLATFORM)
    if not release:
        raise HTTPException(409, {
            "error": "no_release_available",
            "message": "No Aurem Loop release has been published yet.",
        })

    artifact_bytes = await get_release_artifact_bytes(db, release_id=release["release_id"])
    if not artifact_bytes:
        # The metadata row exists but its artifact is missing — a data
        # integrity gap, never silently served as an empty/partial zip.
        logger.error(
            "harness_download: release %s has no stored artifact",
            release["release_id"],
        )
        raise HTTPException(503, "Release artifact temporarily unavailable")

    token = await issue_harness_token(db, user_id=user_id)
    package = inject_identity_into_zip(artifact_bytes, bin=user_id, token=token)

    plan = user.get("tier") or "free"
    await db.harness_downloads.insert_one({
        "grant_id": f"dl_{uuid.uuid4().hex[:12]}",
        "user_id": user_id,
        "release_id": release["release_id"],
        "version": release["version"],
        "plan": plan,
        "format": "zip",
        "issued_at": time.time(),
    })

    filename = f"aurem-loop-{release['version']}.zip"
    return Response(
        content=package,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/harness/download-exe")
async def harness_download_exe(authorization: str | None = Header(None)) -> Response:
    """Gated .exe installer download — same sign-in gate as the zip.
    Serves the .exe byte-for-byte (no identity injection possible).
    A release with no .exe attached yet returns 409, never a zip
    relabelled as an .exe.
    """
    user = await current_dev(authorization)
    user_id = user["user_id"]

    allowed = await check_rate_limit_async(
        f"harness_download_exe:{user_id}", _DOWNLOAD_RATE_LIMIT_PER_MINUTE,
    )
    if not allowed:
        raise HTTPException(429, "Too many download attempts, try again shortly")

    db = require_db()

    release = await get_latest_release(db, platform=_PLATFORM)
    if not release:
        raise HTTPException(409, {
            "error": "no_release_available",
            "message": "No Aurem Loop release has been published yet.",
        })

    exe_bytes = await get_release_exe_bytes(db, release_id=release["release_id"])
    if not exe_bytes:
        raise HTTPException(409, {
            "error": "no_exe_available",
            "message": "No .exe installer has been attached to this release yet.",
        })

    plan = user.get("tier") or "free"
    await db.harness_downloads.insert_one({
        "grant_id": f"dl_{uuid.uuid4().hex[:12]}",
        "user_id": user_id,
        "release_id": release["release_id"],
        "version": release["version"],
        "plan": plan,
        "format": "exe",
        "issued_at": time.time(),
    })

    filename = exe_filename_for_version(release["version"])
    return Response(
        content=exe_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Aurem-Package-SHA256": release.get("exe_sha256") or "",
        },
    )
