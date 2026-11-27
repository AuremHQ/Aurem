"""
services/seo/framework_detect.py — framework detection (spec §5 detect.py).

next    → package.json deps has "next"
react   → package.json deps has "react", no SSR (no "next")
static  → *.html present, no package.json
wordpress → wp-content/ or wp-includes/ in the file tree
shopify  → a .liquid file or config/settings_schema.json in the tree
unknown → none of the above; treated as static + PR-body note (§14)
"""
from __future__ import annotations


def detect_framework(file_tree: list[str], package_json: dict | None) -> tuple[str, bool]:
    """Returns (framework, was_unknown_fallback)."""
    deps = {}
    if package_json:
        deps = {**(package_json.get("dependencies") or {}),
                **(package_json.get("devDependencies") or {})}
    if "next" in deps:
        return "next", False
    if "react" in deps:
        return "react", False
    if any(p.startswith("wp-content/") or p.startswith("wp-includes/") for p in file_tree):
        return "wordpress", False
    if any(p.endswith(".liquid") or p == "config/settings_schema.json" for p in file_tree):
        return "shopify", False
    if package_json is None and any(f.endswith(".html") for f in file_tree):
        return "static", False
    if package_json is None:
        return "static", True  # §14 — unknown → static injection + PR note
    return "static", True
