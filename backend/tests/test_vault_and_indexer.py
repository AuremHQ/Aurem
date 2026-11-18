"""
test_vault_and_indexer.py — Iter 31 smoke tests for the imported
modules.

_maybe_decrypt tests: tokens are stored in plaintext (crypto removed 2026-11-17), so there is no vault and no fail-closed contract.
"""
from __future__ import annotations

import pytest

from services.repo_indexing import _maybe_decrypt
from services.codebase_indexer import (
    _detect_lang, _detect_role, _parse_repo_url, _format_context_block,
)


# ─── codebase_indexer (pure unit) ────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("backend/routers/auth.py",      "routes"),
    ("backend/main.py",               "other"),
    ("backend/server.py",             "routes"),
    ("app/models/user.py",            "models"),
    ("app/schemas/order.py",          "models"),
    ("frontend/src/components/x.jsx", "components"),
    ("frontend/src/pages/y.tsx",      "components"),
    ("requirements.txt",              "deps"),
    ("package.json",                  "deps"),
    ("README.md",                     "other"),
])
def test_role_detection(path, expected):
    assert _detect_role(path) == expected


@pytest.mark.parametrize("path,expected", [
    ("a.py",     "python"),
    ("b.jsx",    "js"),
    ("c.tsx",    "js"),
    ("d.json",   "json"),
    ("e.md",     "md"),
    ("f.yaml",   "yaml"),
    ("g.txt",    "other"),
])
def test_lang_detection(path, expected):
    assert _detect_lang(path) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/me/repo",          ("me", "repo")),
    ("https://github.com/me/repo.git",      ("me", "repo")),
    ("git@github.com:me/repo.git",          ("me", "repo")),
    ("github.com/teji/aurem",               ("teji", "aurem")),
])
def test_parse_repo_url(url, expected):
    assert _parse_repo_url(url) == expected


def test_parse_repo_url_invalid_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _parse_repo_url("not a url")
    assert ei.value.status_code == 400


def test_format_context_block_trims_and_groups():
    doc = {
        "repo_owner": "me", "repo_name": "demo",
        "default_branch": "main", "file_count": 3,
        "deps": {"python": ["fastapi", "pydantic"], "node": []},
        "files": [
            {"path": "routers/auth.py", "role": "routes", "lang": "python", "snippet": "def login(): pass"},
            {"path": "models/user.py", "role": "models", "lang": "python", "snippet": "class User: pass"},
            {"path": "components/Btn.jsx", "role": "components", "lang": "js", "snippet": "export const Btn=()=>null"},
        ],
    }
    block = _format_context_block(doc, max_chars=4000)
    assert "routers/auth.py" in block
    assert "models/user.py" in block
    assert "components/Btn.jsx" in block
    assert "ROUTES" in block
    assert "MODELS" in block
    assert "COMPONENTS" in block
    # Hard cap honoured
    short = _format_context_block(doc, max_chars=100)
    assert len(short) <= 100 + len("\n…(context trimmed)")


# ——— repo_indexing._maybe_decrypt (plaintext passthrough) ———


@pytest.mark.asyncio
async def test_maybe_decrypt_returns_plaintext_token():
    """Tokens are stored without encryption — passthrough unchanged."""
    assert await _maybe_decrypt("user-1", "ghp_plaintexttoken") == "ghp_plaintexttoken"


@pytest.mark.asyncio
async def test_maybe_decrypt_returns_none_for_empty():
    assert await _maybe_decrypt("user-1", None) is None
    assert await _maybe_decrypt("user-1", "") is None


@pytest.mark.asyncio
async def test_maybe_decrypt_rejects_legacy_v1_ciphertext():
    """A legacy 'v1:' Fernet ciphertext can no longer be decrypted."""
    assert await _maybe_decrypt("user-1", "v1:gAAAAABlegacy-ciphertext") is None


@pytest.mark.asyncio
async def test_maybe_decrypt_never_raises():
    assert await _maybe_decrypt("user-1", "ghp_x") == "ghp_x"
