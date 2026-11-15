"""
routers/harness_downloads.py — Aurem Loop harness, Phase B download.

`POST /harness/download` — a logged-in website user (free or paid)
downloads the current published Windows release, with a freshly-
issued, per-installation identity.json baked into the zip. Mounted at
`/api/aurem-dev` (standard JWT-auth convention for this codebase) —
unlike routers/harness.py's verify endpoint, there is no pre-existing
external client contract pinning this path, since it's called by the
logged-in browser, not the harness binary.

Hybrid model: there is only ONE package per platform. Free vs paid is
never encoded by shipping different zip contents — it's enforced at
harness startup by the Phase A verify endpoint reading the live tier.
Re-downloading issues a new, independent token; it never revokes an
existing installation (see services.harness_auth.issue_harness_token).

No release published yet -> a clean 409, never a fabricated package.

Fix B: the response carries `X-Aurem-Package-SHA256` — the SHA-256
(lowercase hex) of the EXACT bytes sent. The base zip's static hash
can never match the delivered file, because inject_identity_into_zip
rebuilds the zip with a per-user identity.json; the page shows the
delivered hash (from this header) next to the relabelled base hash.
The hash is computed over the bytes only — it is never stored
against the token and the raw token never appears in a header or log.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Response

from cto_services.auth import current_dev
from cto_services.db import require_db
from services.harness_auth import issue_harness_token
from services.harness_releases import (
    get_latest_release,
    get_release_artifact_bytes,
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

    # Fix B: hash the EXACT bytes being sent (per-user identity.json
    # already baked in), so the browser can verify the file it got.
    # Never logged, never stored — a digest of the bytes, not the token.
    package_sha256 = hashlib.sha256(package).hexdigest()

    plan = user.get("tier") or "free"
    await db.harness_downloads.insert_one({
        "grant_id": f"dl_{uuid.uuid4().hex[:12]}",
        "user_id": user_id,
        "release_id": release["release_id"],
        "version": release["version"],
        "plan": plan,
        "issued_at": time.time(),
    })

    filename = f"aurem-loop-{release['version']}.zip"
    return Response(
        content=package,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Aurem-Package-SHA256": package_sha256,
        },
    )
