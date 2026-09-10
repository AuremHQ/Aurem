"""
services/video_pipeline/lipsync_fal.py — 2026-09-10, fal.ai LatentSync
lip-sync, with two credential modes:

  - Universal Key (default): backend authenticates to the Emergent
    integration proxy with EMERGENT_LLM_KEY, billed to the Integration
    Wallet. Nothing for the admin to configure.
  - BYOK: if the admin pastes their own FAL_KEY in Admin Settings →
    Video generation keys, calls go directly to fal.ai via the
    official `fal_client` SDK instead, billed to their own Fal
    account. Never mixes credentials/routes between the two modes.

Verified live against fal's own catalog (builder-time only, not
repeated at runtime): endpoint_id="fal-ai/latentsync", input
{video_url, audio_url, guidance_scale, seed, loop_mode}, output
{video: {url, file_name, file_size, content_type}}.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional
from urllib.parse import urlparse

import fal_client
import requests

ENDPOINT_ID = "fal-ai/latentsync"


class LipsyncError(Exception):
    pass


class InsufficientCreditsError(LipsyncError):
    pass


async def run_lipsync(*, video_url: str, audio_url: str,
                        fal_api_key: Optional[str] = None, timeout_s: int = 600) -> str:
    """Returns the resulting synced video's URL. Uses BYOK (direct
    fal.ai via the official SDK) when `fal_api_key` is set, otherwise
    falls back to the Emergent Universal Key via the integration
    proxy — never mixes the two."""
    if fal_api_key:
        return await _run_lipsync_byok(video_url=video_url, audio_url=audio_url,
                                         fal_api_key=fal_api_key, timeout_s=timeout_s)
    return await asyncio.to_thread(
        _run_lipsync_universal_key, video_url=video_url, audio_url=audio_url, timeout_s=timeout_s)


async def _run_lipsync_byok(*, video_url: str, audio_url: str,
                              fal_api_key: str, timeout_s: int) -> str:
    """Direct fal.ai call with the admin's own FAL_KEY — never uses
    EMERGENT_LLM_KEY or the integration proxy in this path."""
    client = fal_client.AsyncClient(key=fal_api_key)
    try:
        handle = await client.submit(
            ENDPOINT_ID, arguments={"video_url": video_url, "audio_url": audio_url, "loop_mode": "loop"})
        result = await asyncio.wait_for(handle.get(), timeout=timeout_s)
    except fal_client.FalClientHTTPError as e:
        if getattr(e, "status_code", None) == 401:
            raise LipsyncError("fal_auth_failed — check the fal.ai key in Admin Settings") from e
        raise LipsyncError(f"lip_sync_failed: {e}") from e
    except asyncio.TimeoutError as e:
        raise LipsyncError("timed_out_waiting_for_lip_sync") from e
    video_url_out = (result.get("video") or {}).get("url")
    if not video_url_out:
        raise LipsyncError("lip_sync_completed_but_no_video_url_in_result")
    return video_url_out


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


def _run_lipsync_universal_key(*, video_url: str, audio_url: str, timeout_s: int = 600) -> str:
    """Blocking — called via asyncio.to_thread by run_lipsync above.
    Talks ONLY to the Emergent integration proxy's Fal queue-inference
    contract — never to api.fal.ai directly, never with a FAL_KEY."""
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
