"""Decision 4/13 (2026-11-26) — ring-buffer host permission is `<all_urls>`.

Teji's call (against the narrow recommendation): the Context Bridge
ring buffer must run on the vendor portals the founder actually
captures from, not just auremcto.com. Locks in:

- manifest.json host_permissions == ["<all_urls>"]
- the ring-buffer content script matches == ["<all_urls>"]
- no narrow auremcto.com-only default left anywhere in the manifest
  that could silently re-apply
- the README no longer claims "No `<all_urls>`"

Pure stdlib (json/pathlib) — no backend imports, so this runs even
where the full backend suite cannot (emergentintegrations missing).
"""
import json
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "browser-extension" / "aurem-context-bridge" / "manifest.json"
_README = _REPO_ROOT / "browser-extension" / "aurem-context-bridge" / "README.md"


def _load_manifest():
    assert _MANIFEST.is_file(), f"manifest missing: {_MANIFEST}"
    return json.loads(_MANIFEST.read_text())


def test_host_permissions_are_all_urls():
    manifest = _load_manifest()
    assert manifest["host_permissions"] == ["<all_urls>"], (
        f"host_permissions must be exactly ['<all_urls>'], got "
        f"{manifest.get('host_permissions')}"
    )


def test_content_script_matches_all_urls():
    manifest = _load_manifest()
    scripts = manifest.get("content_scripts", [])
    assert len(scripts) == 1, f"expected exactly 1 content script, got {len(scripts)}"
    assert scripts[0]["matches"] == ["<all_urls>"], (
        f"content script matches must be ['<all_urls>'], got {scripts[0].get('matches')}"
    )
    assert scripts[0]["js"] == ["src/content-ring-buffer.js"], (
        "the script running on every page must be the ring buffer, got "
        f"{scripts[0].get('js')}"
    )
    assert scripts[0].get("run_at") == "document_start"


def test_no_narrow_default_left_in_manifest():
    raw = _MANIFEST.read_text()
    assert "auremcto.com" not in raw, (
        "narrow auremcto.com-only default still present in manifest.json"
    )
    assert "bin-context-pat" not in raw, (
        "stale preview-origin default still present in manifest.json"
    )


def test_readme_matches_all_urls():
    text = _README.read_text()
    assert "No\n  `<all_urls>`" not in text and "No `<all_urls>`" not in text, (
        "README still claims the extension has no <all_urls> permission"
    )
    assert "<all_urls>" in text, "README must document the <all_urls> permission"
