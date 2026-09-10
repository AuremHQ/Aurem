"""
services/video_pipeline/musetalk_job.py — 2026-09-10, RunPod Serverless
dispatch for a MuseTalk (audio-driven lip-sync) worker endpoint.

Worker contract (per RunPod Hub MuseTalk listing — adjust the three
input keys below if a deployed listing's README differs):
  input:  {video_url, audio_url, bbox_shift}
  output: {video_url}  (or nested — see _extract_video_url, which
           tolerates a few common shapes worker templates use)
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

import httpx

POLL_INTERVAL_S = 3.0
POLL_TIMEOUT_S = 300.0  # 5 min ceiling — cold start + inference


class MuseTalkError(Exception):
    pass


def _url(endpoint_id: str, path: str) -> str:
    return f"https://api.runpod.ai/v2/{endpoint_id}/{path}"


async def submit_job(*, video_url: str, audio_url: str, api_key: str,
                       endpoint_id: str) -> str:
    payload = {"input": {"video_url": video_url, "audio_url": audio_url, "bbox_shift": 0}}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(_url(endpoint_id, "run"), headers=headers, json=payload)
    if r.status_code == 401:
        raise MuseTalkError("runpod_auth_failed — check the API key in Admin Settings")
    if r.status_code >= 400:
        raise MuseTalkError(f"runpod_submit_failed_{r.status_code}")
    data = r.json()
    job_id = data.get("id")
    if not job_id:
        raise MuseTalkError("runpod_did_not_return_job_id")
    return job_id


def _extract_video_url(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        for key in ("video_url", "output_video_url", "mp4_url", "url"):
            found = _extract_video_url(value.get(key))
            if found:
                return found
        for nested in value.values():
            found = _extract_video_url(nested)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _extract_video_url(item)
            if found:
                return found
    return None


async def wait_for_result(
    job_id: str, *, api_key: str, endpoint_id: str,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None,
) -> str:
    """Poll until COMPLETED and return the output video URL, or raise."""
    headers = {"Authorization": f"Bearer {api_key}"}
    elapsed = 0.0
    async with httpx.AsyncClient(timeout=30) as client:
        while elapsed < POLL_TIMEOUT_S:
            r = await client.get(_url(endpoint_id, f"status/{job_id}"), headers=headers)
            if r.status_code >= 400:
                raise MuseTalkError(f"runpod_status_failed_{r.status_code}")
            data = r.json()
            status = data.get("status", "UNKNOWN")
            if on_status:
                await on_status(status)
            if status == "COMPLETED":
                video_url = _extract_video_url(data.get("output"))
                if not video_url:
                    raise MuseTalkError("runpod_completed_but_no_video_url_in_output")
                return video_url
            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise MuseTalkError(f"runpod_job_{status.lower()}")
            await asyncio.sleep(POLL_INTERVAL_S)
            elapsed += POLL_INTERVAL_S
    raise MuseTalkError("runpod_poll_timeout")
