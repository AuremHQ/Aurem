"""
2026-09-22 — Founder requests, homepage:
  1. Two competing promos in the hero ("First-50 · X/50 free Pro
     trials" + "X/500 free SEO fixes") diluted each other's message.
     Fix: removed FounderOfferPill from Landing.jsx's hero — First-50
     is now the single, clear hero offer. The SEO-fix offer still
     correctly targets its real audience (signed-up, repo-not-connected
     users) via ConnectRepoBanner.jsx and Projects.jsx's own header.
  2. New /vs/vibe-coders comparison page — reuses the existing
     /vs/:slug template (VsPage.jsx + data/competitors.mjs), extended
     with a "group mode" (`isGroup: true` + `competitors` array) so
     one page can compare ORA against 3 competitors (Lovable, Replit,
     Emergent) in a single N-column table, instead of the usual
     2-column single-competitor layout.
"""
import os

FRONTEND_SRC = "/app/frontend/src"


def _read(path):
    with open(os.path.join(FRONTEND_SRC, path)) as f:
        return f.read()


def test_hero_has_no_founder_offer_pill():
    """2026-09-26 — the First-50 promo chip this test used to also
    check for has since been fully removed from the codebase (0 users
    had ever claimed it). FounderOfferPill staying out of the hero is
    still the valid, standing assertion."""
    src = _read("pages/Landing.jsx")
    assert 'import FounderOfferPill' not in src
    assert '<FounderOfferPill' not in src


def test_vibe_coders_group_entry_exists_with_three_competitors():
    src = _read("data/competitors.mjs")
    assert 'COMPETITORS["vibe-coders"]' in src
    assert "isGroup: true" in src
    for name in ["Lovable", "Replit", "Emergent"]:
        assert f'"{name}"' in src or f"name: \"{name}\"" in src, f"{name} missing from vibe-coders entry"


def test_vspage_renders_group_mode_generically():
    src = _read("pages/VsPage.jsx")
    assert "c.isGroup" in src
    assert "c.pickThemGroups" in src
    assert "c.competitors" in src


def test_seo_prerender_group_mode_symmetric_with_vspage():
    with open("/app/frontend/scripts/seo-prerender.mjs") as f:
        src = f.read()
    assert "c.isGroup" in src
    assert "c.pickThemGroups" in src
