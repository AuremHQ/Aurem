"""
services/video_pipeline/compositor.py — 2026-09-10, ffmpeg hybrid
compositor for ORA Chat video generation.

Builds the 3-phase 1080x1920 vertical layout from a SINGLE MuseTalk
talking-head clip (same clip reused at different crops per phase —
not three separate source renders):

  0 .. hook_end          fullscreen avatar (hook)
  hook_end .. cta_start  30% avatar strip (top) + 70% terminal
                         captions (bottom), driven by Deepgram word
                         timestamps
  cta_start .. duration  fullscreen avatar again (CTA — carried by
                         the script's own closing line, no hardcoded
                         overlay text)

Clips <=8s skip the 3-phase split entirely (too short to read) and
are just re-encoded fullscreen.
"""
from __future__ import annotations

import asyncio
import json

FONT_REGULAR = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"
CANVAS_W, CANVAS_H = 1080, 1920
TOP_H = 576  # 30% of 1920, the avatar strip height during the split phase


class CompositorError(Exception):
    pass


async def _run(cmd: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise CompositorError(f"ffmpeg failed: {stderr.decode(errors='ignore')[-1500:]}")


async def probe_duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise CompositorError(f"ffprobe failed: {err.decode(errors='ignore')[-500:]}")
    return float(json.loads(out.decode())["format"]["duration"])


def _escape_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\").replace(":", "\\:")
    text = text.replace("'", "\\'").replace("%", "\\%")
    return text


def _chunk_captions(words: list[dict], window_start: float, window_end: float,
                      chunk_size: int = 5) -> list[dict]:
    in_window = [w for w in words if w["start"] >= window_start and w["end"] <= window_end]
    chunks = []
    for i in range(0, len(in_window), chunk_size):
        group = in_window[i:i + chunk_size]
        if not group:
            continue
        chunks.append({
            "text": " ".join(w["word"] for w in group),
            "start": group[0]["start"], "end": group[-1]["end"],
        })
    return chunks


async def build_final_video(*, talking_head_path: str, words: list[dict],
                              out_path: str) -> float:
    """Returns final duration in seconds. Raises CompositorError on failure."""
    duration = await probe_duration(talking_head_path)

    if duration <= 8.0:
        cmd = [
            "ffmpeg", "-y", "-i", talking_head_path,
            "-vf", f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                    f"crop={CANVAS_W}:{CANVAS_H},setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", out_path,
        ]
        await _run(cmd)
        return duration

    hook_end = min(4.0, duration * 0.15)
    cta_span = min(10.0, duration * 0.25)
    cta_start = max(hook_end + 1.0, duration - cta_span)
    caption_chunks = _chunk_captions(words, hook_end, cta_start)

    filters = [
        f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{CANVAS_H},setsar=1[full]",
        f"[0:v]scale={CANVAS_W}:{TOP_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{TOP_H},setsar=1[top]",
        f"color=c=0x0d0f12:s={CANVAS_W}x{CANVAS_H}:d={duration}[bg]",
        f"[bg][top]overlay=0:0:enable='between(t,{hook_end},{cta_start})'[with_top]",
        f"[with_top][full]overlay=0:0:"
        f"enable='between(t,0,{hook_end})+between(t,{cta_start},{duration})'[with_full]",
    ]
    last = "with_full"
    header_txt = _escape_drawtext("$ aurem ship --changelog")
    filters.append(
        f"[{last}]drawtext=fontfile={FONT_BOLD}:text='{header_txt}':"
        f"fontsize=34:fontcolor=0x6ee7a0:x=48:y={TOP_H + 48}:"
        f"enable='between(t,{hook_end},{cta_start})'[cap0]"
    )
    last = "cap0"
    for i, chunk in enumerate(caption_chunks):
        txt = _escape_drawtext(chunk["text"])
        node = f"cap{i + 1}"
        filters.append(
            f"[{last}]drawtext=fontfile={FONT_REGULAR}:text='{txt}':"
            f"fontsize=46:fontcolor=white:box=1:boxcolor=0x1a1d22@0.85:boxborderw=16:"
            f"x=(w-text_w)/2:y=1150:"
            f"enable='between(t,{chunk['start']},{chunk['end']})'[{node}]"
        )
        last = node

    cmd = [
        "ffmpeg", "-y", "-i", talking_head_path,
        "-filter_complex", ";".join(filters),
        "-map", f"[{last}]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest", out_path,
    ]
    await _run(cmd)
    return duration
