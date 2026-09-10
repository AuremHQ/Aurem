"""
services/video_pipeline/pipeline.py — 2026-09-10, end-to-end
orchestration for one ORA Chat video-generation job: Deepgram Aura-2
TTS -> fal.ai LatentSync lip-sync (Emergent Universal Key) -> ffmpeg
hybrid compositor -> R2 upload.

Runs as a fire-and-forget `asyncio.create_task` from
routers/admin_video_pipeline.py (same background-job pattern as
routers/fix_pipeline.py::_run_bulk_job). Every stage transition is
persisted to db.video_pipeline_jobs so the frontend can poll
GET /admin/video/jobs/{id} for live status.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
import uuid

import httpx

from services.video_pipeline import compositor, lipsync_fal, secrets_store, storage, tts

logger = logging.getLogger("video_pipeline")

STATIC_AVATAR_DURATION_S = 8.0  # photo avatars -> a short static loop clip


async def _set(db, job_id: str, **fields) -> None:
    fields["updated_at"] = time.time()
    await db.video_pipeline_jobs.update_one({"job_id": job_id}, {"$set": fields})


async def _download(url: str, out_path: str) -> None:
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("GET", url) as r:
            if r.status_code != 200:
                raise RuntimeError(f"download_failed_{r.status_code}")
            with open(out_path, "wb") as fh:
                async for chunk in r.aiter_bytes(65536):
                    fh.write(chunk)


async def _image_to_static_clip(image_path: str, out_path: str) -> None:
    """LatentSync (like MuseTalk) needs a VIDEO source, not a still
    photo — turn an uploaded photo into a short silent loop clip that
    fal's own `loop_mode="loop"` then stretches to match the audio."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loop", "1", "-i", image_path, "-t", str(STATIC_AVATAR_DURATION_S),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=25",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", out_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"avatar_photo_to_clip_failed: {stderr.decode(errors='ignore')[-500:]}")


async def run_job(db, job_id: str) -> None:
    job = await db.video_pipeline_jobs.find_one({"job_id": job_id})
    if not job:
        return
    tmp_dir = tempfile.mkdtemp(prefix=f"oravideo_{job_id}_")
    try:
        secrets = await secrets_store.get_secrets(db)
        if not secrets.get("deepgram_api_key"):
            raise RuntimeError(
                "deepgram_not_configured — add it in Admin Settings → Video generation keys")

        # 1 — Deepgram Aura-2 TTS + word timestamps (captions only —
        # the lip-sync step is driven by the raw audio, not by these timings).
        await _set(db, job_id, status="synthesizing_audio")
        wav = await tts.synthesize(job["script"], secrets["deepgram_api_key"])
        audio_path = os.path.join(tmp_dir, "speech.wav")
        with open(audio_path, "wb") as fh:
            fh.write(wav)
        words = await tts.word_timestamps(wav, secrets["deepgram_api_key"])

        audio_key = f"{storage.PREFIX}audio/{job_id}.wav"
        storage.upload_file(audio_path, audio_key, "audio/wav")
        audio_url = storage.presign_get(audio_key, expires_in=3600)

        # Photo avatars need converting to a short clip first — video
        # avatars are used as-is.
        if job["avatar_kind"] == "image":
            avatar_local = os.path.join(tmp_dir, "avatar_source")
            await _download(storage.presign_get(job["avatar_r2_key"], expires_in=600), avatar_local)
            static_clip_path = os.path.join(tmp_dir, "avatar_clip.mp4")
            await _image_to_static_clip(avatar_local, static_clip_path)
            clip_key = f"{storage.PREFIX}avatar_clips/{job_id}.mp4"
            storage.upload_file(static_clip_path, clip_key, "video/mp4")
            avatar_video_url = storage.presign_get(clip_key, expires_in=3600)
        else:
            avatar_video_url = storage.presign_get(job["avatar_r2_key"], expires_in=3600)

        # 2 — fal.ai LatentSync lip-sync (BYOK if the admin configured
        # their own fal.ai key, else the Emergent Universal Key).
        await _set(db, job_id, status="lip_syncing")
        talking_head_url = await lipsync_fal.run_lipsync(
            video_url=avatar_video_url, audio_url=audio_url,
            fal_api_key=secrets.get("fal_api_key"))

        talking_head_path = os.path.join(tmp_dir, "talking_head.mp4")
        await _download(talking_head_url, talking_head_path)

        # 3 — ffmpeg hybrid compositor (hook / split-screen captions / CTA)
        await _set(db, job_id, status="compositing", stage_detail=None)
        final_path = os.path.join(tmp_dir, "final.mp4")
        duration = await compositor.build_final_video(
            talking_head_path=talking_head_path, words=words, out_path=final_path)

        # 4 — upload final render
        final_key = f"{storage.PREFIX}final/{job_id}.mp4"
        storage.upload_file(final_path, final_key, "video/mp4")

        await _set(db, job_id, status="done", video_r2_key=final_key, duration_s=duration)
        logger.info("video_pipeline job %s done", job_id)
    except Exception as e:                                          # noqa: BLE001
        logger.exception("video_pipeline job %s failed", job_id)
        await _set(db, job_id, status="failed", error=str(e)[:500])
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def new_job_id() -> str:
    return uuid.uuid4().hex
