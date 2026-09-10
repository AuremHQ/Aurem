"""
services/video_pipeline/lipsync_fal.py — 2026-09-10, fal.ai LatentSync
lip-sync via the Emergent Universal Key integration proxy.

Replaces the earlier RunPod + custom-MuseTalk-Docker-worker design —
fal.ai already hosts LatentSync as a pay-per-use queue endpoint, so
there is no Docker image to build/maintain and no GPU account to
provision. Billed against the Integration Wallet (EMERGENT_LLM_KEY),
not a separate API key — nothing for the admin to configure here.

Verified live against fal's own catalog (builder-time only, not
repeated at runtime): endpoint_id="fal-ai/latentsync", input
{video_url, audio_url, guidance_scale, seed, loop_mode}, output
{video: {url, file_name, file_size, content_type}}.

Per playbook: this Universal Key runtime client talks ONLY to the
Emergent integration proxy's Fal queue-inference contract — never to
api.fal.ai directly, never with a `FAL_KEY`.
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlparse

import requests

ENDPOINT_ID = "fal-ai/latentsync"


class LipsyncError(Exception):
    pass


class InsufficientCreditsError(LipsyncError):
    pass


def _integration_proxy_base() -> str:
    base = os.environ.get("INTEGRATION_PROXY_URL", "https://integrations.emergentagent.com")
    return base.rstrip("/")


def _control_base() -> str:
    return f"{_integration_proxy_base()}/api/v1/fal"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['EMERGENT_LLM_KEY']}",
        "Content-Type": "application/json",
    }


def _trusted_proxy_queue_url(value: str) -> str:
    queue_base = urlparse(f"{_control_base()}/queue")
    candidate = urlparse(value)
    prefix = queue_base.path.rstrip("/") + "/"
    if (candidate.scheme != queue_base.scheme or candidate.netloc != queue_base.netloc
            or candidate.username is not None or candidate.password is not None
            or candidate.fragment or not candidate.path.startswith(prefix)):
        raise LipsyncError("Proxy returned an untrusted media queue URL")
    return value


def _get_with_retry(url: str, *, timeout_s: int, deadline: float, max_attempts: int = 5) -> dict:
    delay = 1.0
    for attempt in range(max_attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LipsyncError("timed_out_waiting_for_lip_sync")
        try:
            r = requests.get(url, headers=_headers(), timeout=min(timeout_s, remaining))
            r.raise_for_status()
            return r.json()
        except (requests.ConnectTimeout, requests.ReadTimeout, requests.ConnectionError):
            if attempt == max_attempts - 1:
                raise LipsyncError("lip_sync_polling_failed_after_retries")
            time.sleep(min(delay, max(deadline - time.monotonic(), 0)))
            delay *= 2
    raise LipsyncError("timed_out_waiting_for_lip_sync")


def run_lipsync(*, video_url: str, audio_url: str, timeout_s: int = 600) -> str:
    """Blocking — call via asyncio.to_thread from async code. Returns
    the resulting synced video's URL."""
    payload: dict[str, Any] = {"video_url": video_url, "audio_url": audio_url, "loop_mode": "loop"}
    submit = requests.post(
        f"{_control_base()}/proxy",
        headers={**_headers(), "X-Fal-Target-Url": f"https://queue.fal.run/{ENDPOINT_ID}"},
        json=payload, timeout=60,
    )
    if submit.status_code == 402:
        raise InsufficientCreditsError(
            "Insufficient Universal Key credits — top up in Profile → Manage plan → Universal Key")
    submit.raise_for_status()
    submission = submit.json()
    status_url = _trusted_proxy_queue_url(submission["status_url"])
    response_url = _trusted_proxy_queue_url(submission["response_url"])

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status_body = _get_with_retry(status_url, timeout_s=30, deadline=deadline)
        status = (status_body.get("status") or "").upper()
        if status in {"COMPLETED", "OK"}:
            if status_body.get("error"):
                raise LipsyncError(f"lip_sync_failed: {status_body['error']}")
            result = _get_with_retry(response_url, timeout_s=60, deadline=deadline)
            video_url_out = (result.get("video") or {}).get("url")
            if not video_url_out:
                raise LipsyncError("lip_sync_completed_but_no_video_url_in_result")
            return video_url_out
        if status in {"FAILED", "CANCELLED", "CANCELED", "ERROR"}:
            raise LipsyncError(f"lip_sync_job_{status.lower()}")
        time.sleep(2)
    raise LipsyncError("timed_out_waiting_for_lip_sync")
