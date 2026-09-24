"""
2026-09-12 — live-production bug regression (PR #386, TJSNDHU/Aurem).

Founder ran Visibility Kit's batch Apply on a real repo with 234 real
pages already in sitemap.xml, but a stale/placeholder `scan_urls` on
the project. The resulting PR deleted all 234 real <url> entries and
added 2 placeholder ("yourdomain.com") ones — because merge_lastmod()
ignored existing_xml entirely and rebuilt purely from `urls`.

Caught in time only because the PR stayed in Draft — this test locks
in the real fix: existing real entries must always survive a re-apply,
even when the fresh scan is empty or full of placeholder junk.
"""
from services.visibility import sitemap as sitemap_gen


def _existing_sitemap_with(n: int) -> str:
    urls = [{"loc": f"https://real-site.com/page-{i}"} for i in range(n)]
    return sitemap_gen.render_sitemap(urls, "2026-08-01")


def test_stale_empty_scan_does_not_wipe_real_sitemap():
    existing = _existing_sitemap_with(234)
    merged = sitemap_gen.merge_lastmod(existing, [], "2026-09-12")
    assert merged.count("<url>") == 234
    for i in range(234):
        assert f"https://real-site.com/page-{i}" in merged


def test_placeholder_scan_urls_are_filtered_and_real_entries_survive():
    existing = _existing_sitemap_with(3)
    placeholder_scan = [{"loc": "https://yourdomain.com/"}, {"loc": "https://example.com/foo"}]
    merged = sitemap_gen.merge_lastmod(existing, placeholder_scan, "2026-09-12")
    assert merged.count("<url>") == 3
    assert "yourdomain.com" not in merged
    assert "example.com" not in merged


def test_fresh_real_urls_are_added_on_top_of_existing():
    existing = _existing_sitemap_with(2)
    merged = sitemap_gen.merge_lastmod(
        existing, [{"loc": "https://real-site.com/page-2"}], "2026-09-12",
    )
    assert merged.count("<url>") == 3
    assert "https://real-site.com/page-0" in merged
    assert "https://real-site.com/page-2" in merged


def test_no_existing_sitemap_still_generates_from_fresh_urls():
    merged = sitemap_gen.merge_lastmod(
        None, [{"loc": "https://real-site.com/a"}], "2026-09-12",
    )
    assert merged.count("<url>") == 1
    assert "https://real-site.com/a" in merged
