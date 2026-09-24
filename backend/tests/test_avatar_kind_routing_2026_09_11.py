"""
tests/test_avatar_kind_routing_2026_09_11.py — regression coverage for
the 2026-09-11 architecture fix: photo avatars produced a completely
static "photo with audio" (zero head/eye/mouth movement — confirmed
via a founder-uploaded sample and reproduced by tracing the pipeline)
because LatentSync only re-dubs the MOUTH on footage that's already
moving, and a still photo (previously converted to a frozen ffmpeg
loop clip) has no motion for it to work with.

Fix: `avatar_kind == "image"` now calls fal-ai/kling-video/ai-avatar/v2
(image-to-video, animates a still photo directly from audio) instead
of being pre-converted to a static clip and fed to LatentSync.
`avatar_kind == "video"` is unaffected (LatentSync remains correct
there — the user's own footage already has natural movement).

Live-verified separately (not re-run here, too slow/costly for CI):
a real fal-ai/kling-video/ai-avatar/v2 call against a still photo
produced a video independently confirmed (via multimodal analysis) to
have "natural-looking head motion, realistic blinking, and lip-sync
synchronized with speech" — genuinely animated, not static.
"""
import inspect

from services.video_pipeline import lipsync_fal, pipeline


def test_lipsync_fal_exposes_both_endpoints():
    assert lipsync_fal.LATENTSYNC_ENDPOINT_ID == "fal-ai/latentsync"
    assert lipsync_fal.KLING_AVATAR_ENDPOINT_ID == "fal-ai/kling-video/ai-avatar/v2/standard"
    assert callable(lipsync_fal.run_lipsync)
    assert callable(lipsync_fal.run_avatar_animation)


def test_run_avatar_animation_signature_matches_kling_contract():
    # image_url + audio_url are the two required fields per fal's own
    # published schema for this endpoint (verified 2026-09-11).
    sig = inspect.signature(lipsync_fal.run_avatar_animation)
    assert "image_url" in sig.parameters
    assert "audio_url" in sig.parameters
    assert "fal_api_key" in sig.parameters


def test_pipeline_source_routes_image_avatars_to_kling_not_latentsync():
    # Guard against a future edit silently reverting the fix by
    # re-introducing the old static-clip-then-LatentSync path for
    # photo avatars.
    src = inspect.getsource(pipeline.run_job)
    assert 'job["avatar_kind"] == "image"' in src
    assert "run_avatar_animation" in src
    assert "run_lipsync" in src
    # the old ffmpeg "freeze the photo into a clip" helper must be gone
    assert "_image_to_static_clip" not in src
    assert "_image_to_static_clip" not in inspect.getsource(pipeline)
