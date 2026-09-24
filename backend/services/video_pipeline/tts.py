"""
services/video_pipeline/tts.py — 2026-09-10, Deepgram Aura-2 TTS +
word-level caption timestamps for the ORA Chat video-generation
pipeline.

Aura-2 TTS itself returns audio only, no alignment metadata — the
generated WAV is re-transcribed through Deepgram's /listen endpoint
to get word timings for the Phase 3 caption/B-roll overlay. Lip-sync
itself (MuseTalk) is driven directly by the raw audio waveform, NOT
by these timestamps — they exist purely to time the on-screen text.
"""
from __future__ import annotations

import httpx

DEEPGRAM_TTS_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_STT_URL = "https://api.deepgram.com/v1/listen"
TTS_MODEL = "aura-2-thalia-en"
MAX_CHARS = 2000


class TTSError(Exception):
    pass


async def synthesize(text: str, api_key: str) -> bytes:
    text = (text or "").strip()
    if not text:
        raise TTSError("empty_text")
    if len(text) > MAX_CHARS:
        raise TTSError(f"text_too_long — max {MAX_CHARS} characters per scene")
    params = {
        "model": TTS_MODEL, "encoding": "linear16",
        "container": "wav", "sample_rate": "24000",
    }
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json", "Accept": "audio/wav",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(DEEPGRAM_TTS_URL, params=params, headers=headers,
                                json={"text": text})
    if r.status_code == 401:
        raise TTSError("deepgram_auth_failed — check the API key in Admin Settings")
    if r.status_code != 200:
        raise TTSError(f"deepgram_tts_failed_{r.status_code}")
    if not r.content.startswith(b"RIFF"):
        raise TTSError("deepgram_returned_non_wav")
    return r.content


async def word_timestamps(wav: bytes, api_key: str) -> list[dict]:
    """Best-effort — never raises. A failed alignment call just means
    no burned-in captions, not a failed render."""
    params = {"model": "nova-3", "language": "en-US", "smart_format": "true"}
    headers = {"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(DEEPGRAM_STT_URL, params=params, headers=headers, content=wav)
        if r.status_code != 200:
            return []
        data = r.json()
        raw = data["results"]["channels"][0]["alternatives"][0]["words"]
    except Exception:                                                # noqa: BLE001
        return []
    return [
        {"word": w.get("punctuated_word", w.get("word", "")),
         "start": float(w["start"]), "end": float(w["end"])}
        for w in raw
    ]
