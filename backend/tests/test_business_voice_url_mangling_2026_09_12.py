"""
tests/test_business_voice_url_mangling_2026_09_12.py

Regression lock for a live-repro'd bug found in the SAME founder turn
as the "your page" fix (test_business_voice_nonpage_filename_2026_09_12.py):
the commit-confirmation link came back malformed —
"https://website's connection.com/TJSNDHU/Aurem/update/<sha>" instead
of "https://github.com/TJSNDHU/Aurem/update/<sha>".

Root cause: business_voice_filter.py's regex passes (filename
humanization AND the business_voice_map.json word-level map) ran over
the ENTIRE reply text with no concept of "this span is a URL, don't
touch it". "github" inside the domain "github.com" is a whole word by
`\\b` standards, so the map's "github" -> "website's connection" entry
fired on it exactly as it would in a normal sentence. Any URL whose
path happens to end in a dotted extension (e.g. "...report.json") is
equally exposed to _FILENAME_RE. One shared root cause across both
bugs — filter_for_business_owner() had zero URL-awareness.

Fix: every http(s) URL is pulled out first, replaced with a sentinel
placeholder immune to every later regex pass, then restored verbatim
at the very end.
"""
from services.business_voice_filter import filter_for_business_owner


def test_t_github_domain_in_url_not_mangled():
    # Word-for-word live repro (SHA truncated is fine, format matters).
    raw = (
        "Done! Here's the commit: https://github.com/TJSNDHU/Aurem/"
        "update/563c945bc4eb8cb9e6f28300890aa3a7d08ba5af"
    )
    out = filter_for_business_owner(raw)
    assert "https://github.com/TJSNDHU/Aurem/update/563c945bc4eb8cb9e6f28300890aa3a7d08ba5af" in out
    assert "website's connection" not in out.lower()


def test_t_url_with_filename_extension_not_mangled():
    raw = "See the data at https://api.example.com/report.json for details."
    out = filter_for_business_owner(raw)
    assert "https://api.example.com/report.json" in out
    assert "your page" not in out.lower() and "that page" not in out.lower()


def test_t_markdown_link_url_preserved():
    raw = "View it here: [commit](https://github.com/TJSNDHU/Aurem/commit/abc123)."
    out = filter_for_business_owner(raw)
    assert "https://github.com/TJSNDHU/Aurem/commit/abc123" in out


def test_t_non_url_github_mention_still_humanized():
    # Must NOT regress: "github" as a plain word (no URL around it)
    # still gets the existing business-friendly mapping.
    out = filter_for_business_owner("Share your github repo and I will start working on it.")
    assert "github" not in out.lower()
    assert "your website" in out.lower()


def test_t_url_and_nonpage_filename_fix_combine_correctly():
    # Both live-repro'd bugs occurred in the SAME real reply — confirm
    # they don't interfere with each other now that both are fixed.
    raw = (
        "package.json is strict JSON... Let me read package.json first, "
        "here's the commit link https://github.com/TJSNDHU/Aurem/update/abc123"
    )
    out = filter_for_business_owner(raw)
    assert "your page" not in out.lower()
    assert "package.json" not in out
    assert "https://github.com/TJSNDHU/Aurem/update/abc123" in out
