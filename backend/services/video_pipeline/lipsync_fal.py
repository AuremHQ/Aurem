"""
services/video_pipeline/lipsync_fal.py — 2026-09-10/11, fal.ai avatar
animation + lip-sync, with two credential modes:

  - Universal Key (default): backend authenticates to the Emergent
    integration proxy with EMERGENT_LLM_KEY, billed to the Integration
    Wallet. Nothing for the admin to configure.
  - BYOK: if the admin pastes their own FAL_KEY in Admin Settings →
    Video generation keys, calls go directly to fal.ai via the
    official `fal_client` SDK instead, billed to their own Fal
    account. Never mixes credentials/routes between the two modes.

Two DIFFERENT fal endpoints are used depending on the avatar's kind
— this is a deliberate architecture fix (2026-09-11): LatentSync is a
video-to-video "mouth touch-up" model built to refine footage of a
person who is ALREADY moving/talking on camera. Fed a frozen still
photo (our old `_image_to_static_clip` approach), it has no motion
to work with, so photo avatars rendered with ZERO head movement /
blinking / expression — a real founder-reported bug, not a config
issue. Kling AI Avatar v2 is an image-to-video model purpose-built to
animate a STILL PHOTO from audio (head movement, blinking, lip-sync,
expression), so it is used for `avatar_kind == "image"` instead;
LatentSync remains correct for `avatar_kind == "video"` (the user's
own footage already moves — we only need to redub the mouth).

  - run_lipsync(...)          -> fal-ai/latentsync (video avatars)
  - run_avatar_animation(...) -> fal-ai/kling-video/ai-avatar/v2/standard
                                  (photo avatars)

Verified live against fal's own catalog (builder-time only, not
repeated at runtime):
  latentsync:    input {video_url, audio_url, loop_mode}, output {video: {url, ...}}
  kling-avatar:  input {image_url, audio_url}, output {video: {url}}
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional
from urllib.parse import urlparse

import fal_client
import requests

LATENTSYNC_ENDPOINT_ID = "fal-ai/latentsync"
KLING_AVATAR_ENDPOINT_ID = "fal-ai/kling-video/ai-avatar/v2/standard"


class LipsyncError(Exception):
    pass


class InsufficientCreditsError(LipsyncError):
    pass


async def run_lipsync(*, video_url: str, audio_url: str,
                        fal_api_key: Optional[str] = None, timeout_s: int = 600) -> str:
    """Video avatars: re-dub the mouth on EXISTING footage via
    fal-ai/latentsync. Returns the resulting synced video's URL."""
    payload = {"video_url": video_url, "audio_url": audio_url, "loop_mode": "loop"}
    return await _submit(LATENTSYNC_ENDPOINT_ID, payload,
                           fal_api_key=fal_api_key, timeout_s=timeout_s)


async def run_avatar_animation(*, image_url: str, audio_url: str,
                                 fal_api_key: Optional[str] = None, timeout_s: int = 600) -> str:
    """Photo avatars: animate a STILL PHOTO from audio (head
    movement, blinking, lip-sync) via fal-ai/kling-video/ai-avatar/v2.
    Returns the resulting animated video's URL."""
    payload = {"image_url": image_url, "audio_url": audio_url}
    return await _submit(KLING_AVATAR_ENDPOINT_ID, payload,
                           fal_api_key=fal_api_key, timeout_s=timeout_s)


async def _submit(endpoint_id: str, payload: dict, *,
                    fal_api_key: Optional[str], timeout_s: int) -> str:
    if fal_api_key:
        return await _submit_byok(endpoint_id, payload, fal_api_key=fal_api_key, timeout_s=timeout_s)
    return await asyncio.to_thread(
        _submit_universal_key, endpoint_id, payload, timeout_s=timeout_s)


async def _submit_byok(endpoint_id: str, payload: dict, *,
                         fal_api_key: str, timeout_s: int) -> str:
    """Direct fal.ai call with the admin's own FAL_KEY — never uses
    EMERGENT_LLM_KEY or the integration proxy in this path."""
    client = fal_client.AsyncClient(key=fal_api_key)
    try:
        handle = await client.submit(endpoint_id, arguments=payload)
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


def _submit_universal_key(endpoint_id: str, payload: dict[str, Any], *, timeout_s: int = 600) -> str:
    """Blocking — called via asyncio.to_thread by _submit above.
    Talks ONLY to the Emergent integration proxy's Fal queue-inference
    contract — never to api.fal.ai directly, never with a FAL_KEY."""
    submit = requests.post(
        f"{_control_base()}/proxy",
        headers={**_headers(), "X-Fal-Target-Url": f"https://queue.fal.run/{endpoint_id}"},
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
