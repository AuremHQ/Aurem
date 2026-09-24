"""
tests/test_video_pipeline_2026_09_10.py — regression coverage for:
  1. The drawtext apostrophe escaping bug (real ffmpeg failure seen in
     production on captions like "That's it" — see compositor.py's
     _escape_drawtext docstring for the root cause).
  2. Auth-gating on the newer video-pipeline endpoints (avatar
     library, house rules) added after the RunPod -> fal.ai pivot.
"""
import asyncio
import os

import requests

from services.video_pipeline import compositor

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL_FOR_TESTS") or \
    (open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].splitlines()[0].strip()
     if os.path.exists("/app/frontend/.env") else "http://localhost:8001")
API = f"{BASE_URL}/api/aurem-dev"


def test_escape_drawtext_apostrophe():
    # A prior version escaped `'` as `\'`, which ffmpeg's filtergraph
    # parser treats as "end the quoted string here" (backslash is
    # NOT an escape char inside single quotes), corrupting the whole
    # filter graph on any caption containing an apostrophe.
    assert compositor._escape_drawtext("That's it") == "That'\\''s it"
    assert compositor._escape_drawtext("no apostrophes here") == "no apostrophes here"
    assert compositor._escape_drawtext("multiple 'quoted' words'") == \
        "multiple '\\''quoted'\\'' words'\\''"


def test_build_final_video_survives_apostrophes(tmp_path):
    async def _run():
        src = str(tmp_path / "src.mp4")
        out = str(tmp_path / "out.mp4")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x223344:s=320x568:d=20",
            "-c:v", "libx264", "-preset", "veryfast", src,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()

        words, t = [], 0.0
        for w in "That's it. It's ship day, and that's exactly how we like it.".split():
            words.append({"word": w, "start": t, "end": t + 0.3})
            t += 0.35

        duration = await compositor.build_final_video(
            talking_head_path=src, words=words, out_path=out)
        assert duration > 0
        assert os.path.exists(out) and os.path.getsize(out) > 0

    asyncio.run(_run())


def _login():
    r = requests.post(f"{API}/auth/login",
                       json={"email": "test@aurem.dev", "password": "AuremTest2026!"}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def test_avatar_and_house_rules_endpoints_require_auth():
    for method, path in [
        ("get", "/admin/video/avatars"),
        ("post", "/admin/video/avatars"),
        ("get", "/admin/video/house-rules"),
        ("put", "/admin/video/house-rules"),
    ]:
        r = getattr(requests, method)(f"{API}{path}", timeout=15)
        assert r.status_code == 401, f"{method.upper()} {path} should require auth, got {r.status_code}"


def test_house_rules_roundtrip_and_validation():
    token = _login()
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.put(f"{API}/admin/video/house-rules",
                      json={"enforcement_mode": "not_a_real_mode"}, headers=headers, timeout=15)
    assert r.status_code == 422, r.text

    r = requests.put(f"{API}/admin/video/house-rules",
                      json={"rules_text": "Never use the word banana.", "enforcement_mode": "block"},
                      headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["enforcement_mode"] == "block"

    r = requests.get(f"{API}/admin/video/house-rules", headers=headers, timeout=15)
    assert r.status_code == 200
    assert "banana" in r.json()["rules_text"]


def test_create_job_requires_avatar_id_field():
    token = _login()
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{API}/admin/video/jobs", json={"script": "hello"}, headers=headers, timeout=15)
    assert r.status_code == 422, r.text
