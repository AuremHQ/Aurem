"""
services/harness_releases.py — Aurem Loop harness, Phase B.

Release manifest + signing + per-user download packaging for the
downloadable Aurem Loop harness.

D1 (hosting the real `fix/10of10` package) is NOT approved as of this
writing. Every release created here carries an explicit `source` tag
("test_fixture" vs "muse_fix_branch_approved") so a test artifact can
never be confused with — or silently promoted to — a real one; nothing
in this module reconstructs or re-derives content from the public
`main` branch or any pre-resilience ZIP. A release only ever starts as
a "draft"; publish_release() is a separate, explicit admin action.

Signing: no Windows code-signing certificate exists yet (a separate
procurement decision). Until one exists, "signed" here means an
HMAC-SHA256 over a canonical JSON manifest, keyed by a server-held
secret (HARNESS_RELEASE_SIGNING_SECRET) — a real, verifiable signature
that is still not equivalent to platform code-signing. A checksum
alone (artifact_sha256 without a signature) is never treated as
publisher authentication on its own — both are always computed and
stored together.

Storage: artifact bytes are stored inline (a dedicated collection,
harness_release_artifacts, kept separate from the small, frequently-
listed harness_releases metadata documents) rather than object
storage/CDN — matching the v1 decision that no CDN/signed-URL
infrastructure is needed yet. Capped well under Mongo's 16MB document
limit; a real production artifact approaching that size should move
to object storage, flagged as a scope-of-improvement item, not solved
here.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import time
import uuid
import zipfile

_VALID_SOURCES = frozenset({"test_fixture", "muse_fix_branch_approved"})
_MAX_ARTIFACT_BYTES = 15 * 1024 * 1024
_SIGNING_SECRET_ENV = "HARNESS_RELEASE_SIGNING_SECRET"


def _signing_secret() -> bytes:
    secret = os.environ.get(_SIGNING_SECRET_ENV)
    if not secret:
        raise RuntimeError(
            f"{_SIGNING_SECRET_ENV} is not configured — refusing to sign "
            "a release manifest with no key.",
        )
    return secret.encode("utf-8")


def _canonical_manifest(*, version, platform, artifact_sha256, changelog, mandatory) -> bytes:
    manifest = {
        "version": version,
        "platform": platform,
        "artifact_sha256": artifact_sha256,
        "changelog": list(changelog or []),
        "mandatory": bool(mandatory),
    }
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_manifest(*, version, platform, artifact_sha256, changelog, mandatory) -> str:
    body = _canonical_manifest(
        version=version, platform=platform, artifact_sha256=artifact_sha256,
        changelog=changelog, mandatory=mandatory,
    )
    return hmac.new(_signing_secret(), body, hashlib.sha256).hexdigest()


def verify_manifest_signature(signature: str, *, version, platform,
                               artifact_sha256, changelog, mandatory) -> bool:
    expected = sign_manifest(
        version=version, platform=platform, artifact_sha256=artifact_sha256,
        changelog=changelog, mandatory=mandatory,
    )
    return hmac.compare_digest(expected, signature)


async def create_draft_release(
    db, *, version: str, platform: str, changelog: list, mandatory: bool,
    artifact_bytes: bytes, source: str, created_by: str,
) -> dict:
    """Always creates a DRAFT — never published, never "latest". That
    is a separate, explicit publish_release() call."""
    if source not in _VALID_SOURCES:
        raise ValueError(f"unknown release source: {source!r}")
    if not artifact_bytes:
        raise ValueError("artifact_bytes must not be empty")
    if len(artifact_bytes) > _MAX_ARTIFACT_BYTES:
        raise ValueError(
            f"artifact is {len(artifact_bytes)} bytes, over the "
            f"{_MAX_ARTIFACT_BYTES}-byte v1 inline-storage cap",
        )

    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    signature = sign_manifest(
        version=version, platform=platform, artifact_sha256=artifact_sha256,
        changelog=changelog, mandatory=mandatory,
    )
    release_id = f"rel_{uuid.uuid4().hex[:12]}"
    now = time.time()

    await db.harness_releases.insert_one({
        "release_id": release_id,
        "version": version,
        "platform": platform,
        "artifact_sha256": artifact_sha256,
        "signature": signature,
        "changelog": list(changelog or []),
        "mandatory": bool(mandatory),
        "status": "draft",
        "is_latest": False,
        "created_at": now,
        "published_at": None,
        "created_by": created_by,
        "size_bytes": len(artifact_bytes),
        "source": source,
    })
    await db.harness_release_artifacts.insert_one({
        "release_id": release_id,
        "artifact_bytes": artifact_bytes,
    })
    return {
        "release_id": release_id, "version": version,
        "artifact_sha256": artifact_sha256, "signature": signature,
        "status": "draft",
    }


async def publish_release(db, *, release_id: str) -> dict:
    """The one explicit action that makes a release downloadable.
    Un-publishes any prior "latest" for the same platform — exactly
    one release per platform is ever "is_latest" at a time."""
    release = await db.harness_releases.find_one({"release_id": release_id})
    if not release:
        raise ValueError(f"release not found: {release_id!r}")

    await db.harness_releases.update_many(
        {"platform": release["platform"], "is_latest": True},
        {"$set": {"is_latest": False}},
    )
    await db.harness_releases.update_one(
        {"release_id": release_id},
        {"$set": {
            "status": "published", "is_latest": True,
            "published_at": time.time(),
        }},
    )
    return {"release_id": release_id, "status": "published"}


async def get_latest_release(db, *, platform: str) -> dict | None:
    return await db.harness_releases.find_one(
        {"platform": platform, "is_latest": True, "status": "published"},
    )


async def get_release_artifact_bytes(db, *, release_id: str) -> bytes | None:
    doc = await db.harness_release_artifacts.find_one({"release_id": release_id})
    return doc["artifact_bytes"] if doc else None


def inject_identity_into_zip(artifact_zip_bytes: bytes, *, bin: str, token: str) -> bytes:
    """Returns a NEW in-memory zip: every member of the source archive,
    unchanged, except identity.json which is always (re)written with
    exactly {"bin": bin, "token": token} — any stray identity.json
    already present in the uploaded artifact is dropped first so there
    is never more than one, and it is never one carried over from a
    different user or from the admin's upload.

    Pure in-memory zip manipulation — never touches the real
    filesystem, so a hostile member name (e.g. "../../etc/passwd")
    inside the source archive cannot escape anywhere."""
    src = zipfile.ZipFile(io.BytesIO(artifact_zip_bytes))
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            if item.filename == "identity.json":
                continue
            out.writestr(item, src.read(item.filename))
        out.writestr("identity.json", json.dumps({"bin": bin, "token": token}))
    return out_buf.getvalue()
