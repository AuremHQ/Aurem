"""
tests/test_kit_apply_flag.py — Visibility Kit 2.0 P0-1, B1/B4.

kit_apply_enabled() decision order:
  1. kit_apply_enabled flag ON -> enabled (admin override).
  2. Live URL + verify_kit_live() finds a marker -> enabled, proof
     recorded in kit_live_proofs (the flag itself is NEVER flipped
     from a request path).
  3. No live URL + founder tier or preview/dev env -> enabled
     (default ON; the first apply is the proof run).
  4. Otherwise -> disabled with the router's existing reason string.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.seo.kit_engine import kit_apply_enabled
from services.seo import schema_markup as schema_gen

PROJECT_LIVE = {
    "project_id": "p1", "user_id": "u1",
    "live_url": "https://acme.test",
}
PROJECT_NO_URL = {"project_id": "p1", "user_id": "u1"}


def _fake_db():
    db = MagicMock()
    db.kit_live_proofs.insert_one = AsyncMock()
    return db


async def _http_get_marker(url):
    return 200, f"<html><head>{schema_gen._START}</head></html>"


async def _http_get_404(url):
    return 404, ""


@pytest.mark.asyncio
async def test_apply_flag_on_enables_without_live_check():
    db = _fake_db()
    with patch("services.feature_flags.is_enabled",
               new=AsyncMock(return_value=True)):
        result = await kit_apply_enabled(
            db, user_id="u1", tier="free", project=PROJECT_LIVE)
    assert result == {"enabled": True, "reason": "flag_on", "live_proof": None}
    db.kit_live_proofs.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_apply_live_proof_enables_and_records_proof():
    db = _fake_db()
    with patch("services.feature_flags.is_enabled",
               new=AsyncMock(return_value=False)):
        result = await kit_apply_enabled(
            db, user_id="u1", tier="pro", project=PROJECT_LIVE,
            http_get=_http_get_marker)
    assert result["enabled"] is True
    assert result["reason"] == "live_proof"
    assert result["live_proof"]["marker_found"] == schema_gen._START
    assert db.kit_live_proofs.insert_one.call_count == 1
    proof = db.kit_live_proofs.insert_one.call_args.args[0]
    assert proof["project_id"] == "p1"
    assert proof["live_url"] == "https://acme.test"


@pytest.mark.asyncio
async def test_apply_no_marker_stays_disabled():
    db = _fake_db()
    with patch("services.feature_flags.is_enabled",
               new=AsyncMock(return_value=False)):
        result = await kit_apply_enabled(
            db, user_id="u1", tier="pro", project=PROJECT_LIVE,
            http_get=_http_get_404)
    assert result["enabled"] is False
    assert result["reason"] == "apply_not_proven_live"
    db.kit_live_proofs.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_apply_founder_without_live_url_defaults_on(monkeypatch):
    db = _fake_db()
    monkeypatch.setenv("ENVIRONMENT", "production")
    with patch("services.feature_flags.is_enabled",
               new=AsyncMock(return_value=False)):
        result = await kit_apply_enabled(
            db, user_id="u1", tier="founder", project=PROJECT_NO_URL)
    assert result["enabled"] is True
    assert result["reason"] == "founder_or_preview_default"


@pytest.mark.asyncio
async def test_apply_preview_without_live_url_defaults_on(monkeypatch):
    db = _fake_db()
    monkeypatch.setenv("ENVIRONMENT", "preview")
    with patch("services.feature_flags.is_enabled",
               new=AsyncMock(return_value=False)):
        result = await kit_apply_enabled(
            db, user_id="u1", tier="free", project=PROJECT_NO_URL)
    assert result["enabled"] is True
    assert result["reason"] == "founder_or_preview_default"


@pytest.mark.asyncio
async def test_apply_production_without_live_url_stays_off(monkeypatch):
    db = _fake_db()
    monkeypatch.setenv("ENVIRONMENT", "production")
    with patch("services.feature_flags.is_enabled",
               new=AsyncMock(return_value=False)):
        result = await kit_apply_enabled(
            db, user_id="u1", tier="pro", project=PROJECT_NO_URL)
    assert result["enabled"] is False
    assert result["reason"] == "apply_not_proven_live"
