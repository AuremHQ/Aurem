"""
tests/test_business_voice_nonpage_filename_2026_09_12.py

Regression lock for a live-repro'd bug (TJSNDHU/Aurem@main, user
message asking to add a comment to package.json): _FILENAME_RE in
business_voice_filter.py matches ANY dotted extension it knows about
(html/jsx/tsx/css/py/json/md/yaml/txt/sql/env/sh), but
_PAGE_NAME_HINTS can only ever match page-ish stems (home/about/
contact/footer/header/pricing). A backend/config file like
package.json can never match any hint, so it fell through to the
generic _DEFAULT_PAGE_TERM ("your page") — producing an incoherent
reply where "your page" (implying the business owner's OWN website
page) stood in for a config file 3 times in one response, exactly as
reported live.

Fix: extensions that aren't actually page-like (py/json/md/yaml/txt/
sql/env/sh) no longer get the page-flavored default; they fall
through the existing file->page word map instead, landing on the
deictic "that page" rather than the ownership-implying "your page".
"""
from services.business_voice_filter import filter_for_business_owner


def test_t_nonpage_extension_never_says_your_page():
    # Word-for-word live repro.
    raw = (
        "package.json is strict JSON... I'll add it to a real JS page "
        "instead. Let me read package.json first to confirm, then I'll "
        "put the comment at the top of package.json which is already "
        "loaded in context."
    )
    out = filter_for_business_owner(raw)
    assert "package.json" not in out
    assert "your page" not in out.lower()


def test_t_nonpage_extensions_use_generic_fallback():
    for fname in (
        "requirements.txt", "main.py", "config.yml", "schema.sql",
        "deploy.sh", "README.md",
    ):
        out = filter_for_business_owner(f"Let me check {fname} first.")
        assert fname not in out
        assert "your page" not in out.lower()


def test_t_page_like_extensions_unaffected():
    # Existing behavior for genuinely page-ish files must not regress.
    assert "your homepage" in filter_for_business_owner(
        "Let me check index.html first."
    ).lower()
    assert "your homepage" in filter_for_business_owner(
        "Let me check AuremHomepage.jsx first."
    ).lower()
    assert "the bottom of your page" in filter_for_business_owner(
        "Let me check Footer.tsx first."
    ).lower()
