"""
services/video_pipeline/storage.py — 2026-09-10, R2 upload/presign
helpers for the ORA Chat video-generation pipeline. Reuses the R2
client factory from services/db_backup.py (same account/bucket
already used for Mongo backups) under its own `ora-video/` key
prefix — same import convention as services/rollback_snapshot.py and
services/db_restore.py.
"""
from __future__ import annotations

import os

from services.db_backup import _r2_client

PREFIX = "ora-video/"


def upload_file(local_path: str, key: str, content_type: str) -> None:
    client = _r2_client()
    with open(local_path, "rb") as fh:
        client.put_object(
            Bucket=os.environ["R2_BUCKET"], Key=key, Body=fh, ContentType=content_type,
        )


def upload_bytes(data: bytes, key: str, content_type: str) -> None:
    client = _r2_client()
    client.put_object(
        Bucket=os.environ["R2_BUCKET"], Key=key, Body=data, ContentType=content_type,
    )


def presign_get(key: str, expires_in: int = 3600) -> str:
    client = _r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["R2_BUCKET"], "Key": key},
        ExpiresIn=expires_in,
    )


def delete(key: str) -> None:
    client = _r2_client()
    client.delete_object(Bucket=os.environ["R2_BUCKET"], Key=key)
