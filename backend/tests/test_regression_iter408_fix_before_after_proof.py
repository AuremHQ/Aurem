"""Regression lock for Ticket D (2026-02) — per-fix live before/after
proof, extending services/deploy_verify.py.

Security fence reuse (mission requirement): capture_single_screenshot
must call validate_target_url BEFORE any navigation, use a fresh
BrowserContext (no stored credentials), downloads off, no
--no-sandbox. Verified both by direct source inspection AND live
calls (no mocking) against example.com + an SSRF target below.
"""
from __future__ import annotations

import inspect

import pytest

from services import deploy_verify as dv


def test_capture_single_screenshot_checks_ssrf_fence_before_navigation():
    src = inspect.getsource(dv.capture_single_screenshot)
    fence_idx = src.index("validate_target_url(url)")
    nav_idx = src.index("page.goto(")
    assert fence_idx < nav_idx, "SSRF fence must run before any navigation"


def test_capture_single_screenshot_uses_fresh_context_no_downloads():
    src = inspect.getsource(dv.capture_single_screenshot)
    assert "new_context(" in src
    assert "accept_downloads=False" in src
    # Only the docstring mentions "--no-sandbox" (explaining what we
    # deliberately do NOT pass) — check the actual launch call has no
    # args= list containing it.
    assert 'launch_kwargs = {"headless": True}' in src
    assert "args=" not in src.split("def capture_single_screenshot")[1]


@pytest.mark.asyncio
async def test_live_ssrf_fence_blocks_internal_target_real_call():
    """Real call, no mocking — proves the fence actually fires at
    runtime, not just in source text."""
    r = await dv.capture_single_screenshot("http://169.254.169.254/latest/meta-data/")
    assert r["ok"] is False
    assert r["verdict"] == "fail"
    assert "blocked_ssrf" in r["fail_reason"]
    assert r["screenshot"] is None


@pytest.mark.asyncio
async def test_live_capture_and_diff_of_a_real_public_page():
    """Real call, no mocking — captures https://example.com twice and
    confirms a pixel-identical diff (same page, same run)."""
    r1 = await dv.capture_single_screenshot("https://example.com")
    r2 = await dv.capture_single_screenshot("https://example.com")
    assert r1["ok"] is True and r2["ok"] is True
    assert len(r1["screenshot"]) > 1000

    diff = dv.diff_screenshots(r1["screenshot"], r2["screenshot"])
    assert diff["changed"] is False
    assert diff["diff_percent"] == 0.0


def test_diff_screenshots_honest_state_when_capture_missing():
    diff = dv.diff_screenshots(None, b"fakejpegbytes")
    assert diff["changed"] is None
    assert diff["diff_percent"] is None
    assert "cannot diff" in diff["note"]


def test_diff_screenshots_detects_genuine_difference():
    from io import BytesIO
    from PIL import Image
    buf1, buf2 = BytesIO(), BytesIO()
    Image.new("RGB", (100, 100), (255, 255, 255)).save(buf1, format="JPEG")
    Image.new("RGB", (100, 100), (0, 0, 0)).save(buf2, format="JPEG")
    diff = dv.diff_screenshots(buf1.getvalue(), buf2.getvalue())
    assert diff["changed"] is True
    assert diff["diff_percent"] > 50
    assert "diff_image" in diff


# ── fix_pipeline.py wiring: opt-in, honest, never blocks the fast
#    guardian path ──────────────────────────────────────────────────
def test_guardian_rescan_source_has_no_screenshot_capture():
    """_guardian_rescan is the fast/network-free per-file verify from
    Ticket B — this ticket must not have touched it."""
    from routers import fix_pipeline
    src = inspect.getsource(fix_pipeline._guardian_rescan)
    assert "capture_single_screenshot" not in src
    assert "deploy_verify" not in src


def test_run_bulk_job_before_capture_is_opt_in_on_preview_url():
    from routers import fix_pipeline
    src = inspect.getsource(fix_pipeline._run_bulk_job)
    assert 'proj_doc.get("preview_url")' in src or '.get("preview_url")' in src
    assert "capture_single_screenshot" in src
    assert "diff_screenshots" in src


@pytest.mark.asyncio
async def test_proof_endpoint_404_when_no_proof_stored():
    from unittest.mock import AsyncMock, patch
    from fastapi import HTTPException
    from routers.fix_pipeline import get_fix_before_after_proof

    with patch("routers.fix_pipeline._gate",
               AsyncMock(return_value=({"user_id": "u1"}, {"user_id": "u1"}))), \
         patch("routers.fix_pipeline.get_db", lambda: _FakeDbNoProof()):
        with pytest.raises(HTTPException) as exc_info:
            await get_fix_before_after_proof("job_x", variant="before", authorization="Bearer x")
        assert exc_info.value.status_code == 404


class _FakeDbNoProof:
    class fix_before_after_proof:
        @staticmethod
        async def find_one(*a, **kw):
            return None


# ── Ticket D-UI (2026-02) — mobile viewport + honest always-write +
#    /meta endpoint ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_live_capture_uses_real_mobile_375_viewport():
    """Real call, no mocking — proves `viewport='mobile_375'` actually
    launches a real mobile-width context (not a resized desktop shot)."""
    from services.deploy_verify import DEVICE_VIEWPORTS
    r = await dv.capture_single_screenshot("https://example.com", viewport="mobile_375")
    assert r["ok"] is True
    assert len(r["screenshot"]) > 500
    # A real 375px-wide capture must differ in byte size from the
    # desktop capture of the same page (different viewport -> different
    # rendered image), confirming the viewport kwarg is actually wired
    # through to new_context(), not silently ignored.
    r_desktop = await dv.capture_single_screenshot("https://example.com", viewport="desktop")
    assert DEVICE_VIEWPORTS["mobile_375"]["width"] != DEVICE_VIEWPORTS["desktop"]["width"]
    assert r["screenshot"] != r_desktop["screenshot"]


def test_capture_single_screenshot_viewport_defaults_to_desktop():
    import inspect as _inspect
    sig = _inspect.signature(dv.capture_single_screenshot)
    assert sig.parameters["viewport"].default == "desktop"


def test_run_bulk_job_captures_both_devices_independently():
    from routers import fix_pipeline
    src = inspect.getsource(fix_pipeline._run_bulk_job)
    assert '"desktop"' in src and '"mobile"' in src
    assert "mobile_375" in src
    # Honest-always-write contract: once attempted, a row is written
    # even on total failure (the "crashed" flag below proves this).
    assert "proof_attempted" in src
    assert '"crashed": True' in src


@pytest.mark.asyncio
async def test_proof_meta_endpoint_never_attempted_state():
    from unittest.mock import AsyncMock, patch
    from routers.fix_pipeline import get_fix_before_after_proof_meta

    with patch("routers.fix_pipeline._gate",
               AsyncMock(return_value=({"user_id": "u1"}, {"user_id": "u1"}))), \
         patch("routers.fix_pipeline.get_db", lambda: _FakeDbNoProof()):
        resp = await get_fix_before_after_proof_meta("job_x", authorization="Bearer x")
    assert resp == {"ok": True, "attempted": False}


@pytest.mark.asyncio
async def test_proof_meta_endpoint_could_not_verify_state():
    from unittest.mock import AsyncMock, patch
    from routers.fix_pipeline import get_fix_before_after_proof_meta

    class _FakeDbFailed:
        class fix_before_after_proof:
            @staticmethod
            async def find_one(*a, **kw):
                return {
                    "job_id": "job_x", "user_id": "u1", "attempted": True,
                    "devices": {
                        "desktop": {"before_ok": False, "before_fail_reason": "blocked_ssrf",
                                    "after_ok": False},
                        "mobile": {"before_ok": False, "before_fail_reason": "blocked_ssrf",
                                   "after_ok": False},
                    },
                }
    with patch("routers.fix_pipeline._gate",
               AsyncMock(return_value=({"user_id": "u1"}, {"user_id": "u1"}))), \
         patch("routers.fix_pipeline.get_db", lambda: _FakeDbFailed()):
        resp = await get_fix_before_after_proof_meta("job_x", authorization="Bearer x")
    assert resp["attempted"] is True
    assert resp["devices"]["desktop"]["before_ok"] is False
    assert resp["devices"]["desktop"]["before_fail_reason"] == "blocked_ssrf"
    assert resp["devices"]["desktop"]["has_images"] is False


@pytest.mark.asyncio
async def test_proof_meta_endpoint_success_state_both_devices():
    from unittest.mock import AsyncMock, patch
    from routers.fix_pipeline import get_fix_before_after_proof_meta

    class _FakeDbSuccess:
        class fix_before_after_proof:
            @staticmethod
            async def find_one(*a, **kw):
                return {
                    "job_id": "job_x", "user_id": "u1", "attempted": True,
                    "devices": {
                        "desktop": {"before_ok": True, "after_ok": True, "changed": True,
                                    "diff_percent": 12.5, "before_key": "k1", "after_key": "k2"},
                        "mobile": {"before_ok": True, "after_ok": True, "changed": False,
                                   "diff_percent": 0.0, "before_key": "k3", "after_key": "k4"},
                    },
                }
    with patch("routers.fix_pipeline._gate",
               AsyncMock(return_value=({"user_id": "u1"}, {"user_id": "u1"}))), \
         patch("routers.fix_pipeline.get_db", lambda: _FakeDbSuccess()):
        resp = await get_fix_before_after_proof_meta("job_x", authorization="Bearer x")
    assert resp["devices"]["desktop"]["has_images"] is True
    assert resp["devices"]["desktop"]["diff_percent"] == 12.5
    assert resp["devices"]["mobile"]["has_images"] is True


@pytest.mark.asyncio
async def test_proof_endpoint_respects_device_param():
    from unittest.mock import AsyncMock, patch
    from routers.fix_pipeline import get_fix_before_after_proof

    class _FakeDbDevice:
        class fix_before_after_proof:
            @staticmethod
            async def find_one(*a, **kw):
                return {
                    "job_id": "job_x", "user_id": "u1",
                    "devices": {
                        "desktop": {"before_key": "desk_before"},
                        "mobile": {"before_key": "mob_before"},
                    },
                }
    captured_key = {}

    async def _fake_fetch_receipt(key):
        captured_key["k"] = key
        return b"fakejpeg"

    with patch("routers.fix_pipeline._gate",
               AsyncMock(return_value=({"user_id": "u1"}, {"user_id": "u1"}))), \
         patch("routers.fix_pipeline.get_db", lambda: _FakeDbDevice()), \
         patch("services.preview_capture.fetch_receipt", _fake_fetch_receipt):
        await get_fix_before_after_proof("job_x", variant="before", device="mobile",
                                          authorization="Bearer x")
    assert captured_key["k"] == "mob_before"


@pytest.mark.asyncio
async def test_proof_meta_endpoint_owner_isolation():
    """A different user's job_id must come back as 'never attempted',
    not leak the real attempted/device state."""
    from unittest.mock import AsyncMock, patch
    from routers.fix_pipeline import get_fix_before_after_proof_meta

    class _FakeDbOtherOwner:
        class fix_before_after_proof:
            @staticmethod
            async def find_one(*a, **kw):
                return {"job_id": "job_x", "user_id": "victim", "attempted": True,
                        "devices": {"desktop": {"before_ok": True}}}
    with patch("routers.fix_pipeline._gate",
               AsyncMock(return_value=({"user_id": "attacker", "is_admin": False},
                                        {"user_id": "attacker"}))), \
         patch("routers.fix_pipeline.get_db", lambda: _FakeDbOtherOwner()):
        resp = await get_fix_before_after_proof_meta("job_x", authorization="Bearer x")
    assert resp == {"ok": True, "attempted": False}
