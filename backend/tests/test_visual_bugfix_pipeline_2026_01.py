"""Tests for the Multimodal Visual Bug-Fix pipeline (Phase 1+2) —
routers/visual_bugfix.py. Covers upload/get-image/analyze including
error paths, cross-user isolation, vision OCR verification, and the
graceful empty-candidates fallback."""
from __future__ import annotations

import io
import os
import time

import pytest
import requests
from PIL import Image, ImageDraw

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"
VBF = f"{API}/visual-bugfix"

PRIMARY_EMAIL = "test@aurem.dev"
PRIMARY_PWD = "AuremTest2026!"
SECOND_EMAIL = "free-gate-test-0822@aurem.dev"
SECOND_PWD = "FreeGateTest2026!"


def _login(email: str, pwd: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def token() -> str:
    return _login(PRIMARY_EMAIL, PRIMARY_PWD)


@pytest.fixture(scope="module")
def token2() -> str:
    return _login(SECOND_EMAIL, SECOND_PWD)


def _make_png(text_lines=("Submit Payment", "Checkout", "Error: failed to load"),
              size=(600, 400)) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    y = 40
    for line in text_lines:
        d.text((30, y), line, fill="black")
        y += 60
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------- Upload tests ----------------

def test_upload_valid_png(token):
    png = _make_png()
    files = {"file": ("bug.png", png, "image/png")}
    r = requests.post(f"{VBF}/upload", files=files,
                      headers={"Authorization": f"Bearer {token}"}, timeout=60)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["id"]
    assert j["image_url"].endswith(f"/visual-bugfix/{j['id']}/image")
    assert j["width"] == 600 and j["height"] == 400
    assert isinstance(j["file_hash"], str) and len(j["file_hash"]) == 64
    pytest.report_id = j["id"]  # cached


def test_upload_unsupported_type(token):
    files = {"file": ("bad.txt", b"hello", "text/plain")}
    r = requests.post(f"{VBF}/upload", files=files,
                      headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert r.status_code == 400, r.text
    assert "Unsupported" in r.text or "png" in r.text.lower()


def test_upload_gif_rejected(token):
    # Craft a tiny GIF (unsupported per allowed types)
    img = Image.new("RGB", (10, 10), "red")
    buf = io.BytesIO(); img.save(buf, format="GIF")
    files = {"file": ("x.gif", buf.getvalue(), "image/gif")}
    r = requests.post(f"{VBF}/upload", files=files,
                      headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert r.status_code == 400, r.text


def test_upload_too_large(token):
    # 10MB + 1 byte
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024 + 1)
    files = {"file": ("big.png", big, "image/png")}
    r = requests.post(f"{VBF}/upload", files=files,
                      headers={"Authorization": f"Bearer {token}"}, timeout=120)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"


def test_upload_no_auth():
    files = {"file": ("x.png", _make_png(), "image/png")}
    r = requests.post(f"{VBF}/upload", files=files, timeout=30)
    assert r.status_code == 401, r.text


# ---------------- GET image tests ----------------

def test_get_image_with_auth_header(token):
    rid = getattr(pytest, "report_id", None)
    assert rid, "upload test must run first"
    r = requests.get(f"{VBF}/{rid}/image",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/")
    assert len(r.content) > 100


def test_get_image_with_auth_query(token):
    rid = pytest.report_id
    r = requests.get(f"{VBF}/{rid}/image?auth={token}", timeout=30)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/")
    assert len(r.content) > 100


def test_get_image_cross_user_forbidden(token, token2):
    rid = pytest.report_id
    r = requests.get(f"{VBF}/{rid}/image",
                     headers={"Authorization": f"Bearer {token2}"}, timeout=30)
    assert r.status_code == 404, f"cross-user should 404, got {r.status_code}"


def test_get_image_nonexistent(token):
    r = requests.get(f"{VBF}/nonexistent_report_id_xyz/image",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert r.status_code == 404


# ---------------- Analyze tests ----------------

def test_analyze_owned_report_vision_ocr(token):
    rid = pytest.report_id
    r = requests.post(f"{VBF}/{rid}/analyze",
                      json={"bug_description": "Text on the checkout page looks off",
                            "project_id": None},
                      headers={"Authorization": f"Bearer {token}"}, timeout=120)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True, f"vision failed: {j.get('error')}"
    assert j["status"] == "analyzed"
    for k in ("visual_defects", "extracted_strings", "inferred_dom_elements",
              "suggested_patterns", "candidate_files"):
        assert k in j and isinstance(j[k], list)
    # OCR accuracy: at least one of our known-visible strings should appear
    extracted = " ".join(j["extracted_strings"]).lower()
    hits = sum(1 for kw in ("submit payment", "checkout", "error", "failed to load") if kw in extracted)
    assert hits >= 2, f"expected multiple OCR hits, got extracted_strings={j['extracted_strings']}"
    # No project → candidate_files must be empty (graceful path)
    assert j["candidate_files"] == []


def test_analyze_not_owned_returns_404(token, token2):
    rid = pytest.report_id
    r = requests.post(f"{VBF}/{rid}/analyze",
                      json={"bug_description": "hi"},
                      headers={"Authorization": f"Bearer {token2}"}, timeout=30)
    assert r.status_code == 404, r.text


def test_analyze_nonexistent_returns_404(token):
    r = requests.post(f"{VBF}/nope_does_not_exist/analyze",
                      json={"bug_description": "hi"},
                      headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert r.status_code == 404


def test_analyze_with_bogus_project_graceful(token):
    # Upload fresh, then analyze with a nonexistent project_id → must
    # not 500, candidate_files should just be [] (graceful fallback).
    png = _make_png(("Login", "Password", "Submit"))
    r = requests.post(f"{VBF}/upload", files={"file": ("b.png", png, "image/png")},
                      headers={"Authorization": f"Bearer {token}"}, timeout=60)
    assert r.status_code == 200
    rid = r.json()["id"]
    ar = requests.post(f"{VBF}/{rid}/analyze",
                       json={"bug_description": "layout broken",
                             "project_id": "p_definitely_does_not_exist_xyz"},
                       headers={"Authorization": f"Bearer {token}"}, timeout=120)
    assert ar.status_code == 200, ar.text
    aj = ar.json()
    assert aj["ok"] is True
    assert aj["candidate_files"] == []
