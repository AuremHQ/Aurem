"""
2026-09-22 — Founder bug: Landing page "Watch it ship" video cards
("ORA vs Copilot — head-to-head", "Practical workflow — prompt to
commit", "Reliable shipping, every time", "ORA in 60 seconds") showed
native controls but stayed a grey box at 0:00 forever after clicking
play, never rendering a frame.

Root cause (reproduced via Playwright + video.play()):
`MEDIA_ERR_SRC_NOT_SUPPORTED` / Chromium's
`DEMUXER_ERROR_NO_SUPPORTED_STREAMS`. The MP4s are standard H.264/AAC
(verified with ffprobe — not corrupt, not 404, not a CORS issue as
originally suspected) but the browser hitting this has NO licensed
H.264 decoder (`canPlayType('video/mp4; codecs="avc1..."')` returns
`""`) — the default for open-source Chromium builds (common on
Linux). A single `src=` gives the browser no fallback to try.

Fix: `LazyVideo.jsx` now renders `<source>` children (MP4 first,
VP9/WebM second) instead of a single `src` attribute, and each real
video card on Landing.jsx passes a `webmSrc` pointing at a locally
re-encoded fallback under `frontend/public/videos/`. Verified
end-to-end: in a browser with no H.264 support, playback now
correctly falls through to the WebM source and actually renders
frames (readyState 4, currentTime advancing).
"""
import os

FRONTEND_SRC = "/app/frontend/src"
PUBLIC_VIDEOS = "/app/frontend/public/videos"


def _read(path):
    with open(os.path.join(FRONTEND_SRC, path)) as f:
        return f.read()


def test_lazy_video_uses_source_elements_not_single_src():
    src = _read("components/LazyVideo.jsx")
    assert "<source" in src, "must use <source> children so the browser can fall back"
    assert 'type="video/mp4"' in src
    assert 'type="video/webm"' in src
    # MP4 source must come before the WebM fallback in source order —
    # browsers pick the first <source> they support, and MP4/H.264 is
    # the higher-quality, more universally hardware-decoded choice for
    # the vast majority of real browsers.
    mp4_idx = src.find('type="video/mp4"')
    webm_idx = src.find('type="video/webm"')
    assert 0 < mp4_idx < webm_idx


def test_landing_video_cards_all_have_webm_fallback():
    src = _read("pages/Landing.jsx")
    for webm in ["ora-comparison.webm", "ora-workflow.webm", "ora-reliable.webm", "ora-overview.webm"]:
        assert webm in src, f"{webm} fallback not wired into a Landing.jsx video card"
    assert "webmSrc={v.webmSrc}" in src


def test_webm_fallback_files_exist_and_are_real_vp9():
    import subprocess
    for fname in ["ora-comparison.webm", "ora-workflow.webm", "ora-reliable.webm", "ora-overview.webm"]:
        path = os.path.join(PUBLIC_VIDEOS, fname)
        assert os.path.isfile(path), f"missing fallback file: {path}"
        assert os.path.getsize(path) > 100_000, f"{fname} looks too small to be a real video"
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True,
        )
        assert "vp9" in out.stdout, f"{fname} is not VP9-encoded: {out.stdout}"
