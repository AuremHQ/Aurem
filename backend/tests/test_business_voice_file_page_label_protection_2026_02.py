"""
tests/test_business_voice_file_page_label_protection_2026_02.py

Regression lock for a live-repro'd bug this same engagement (founder
QA report, "sql_injection in validators.py" and similar): the
response text read "The only .that page in your project is .that
page. It's a GIF dimension/size checker for Slack..." — ".that page"
is not a valid filename and is not what business_voice_filter.py's
own `_DEFAULT_FILE_TERM` logic was supposed to produce for a .py file.

TWO separate, real bugs combined to produce this exact text:

1. Leading-dot/relative-path orphaning — `_FILENAME_RE`'s old `\\b`
   anchor at the START of the pattern only matches a transition INTO
   a word character. A real filename referenced with a leading "."
   or "./" (a dotfile, or a relative path) has that leading
   punctuation sit OUTSIDE the match, so it survives untouched and
   ends up glued directly onto whatever humanized label replaces the
   rest of the filename (".that page", "./that file").

2. "file" -> "page" word-map collision — `_humanize_filename`
   correctly picks `_DEFAULT_FILE_TERM` ("that file") for non-page-
   like extensions (.py/.json/.sql/etc), but that OUTPUT then runs
   through the generic dev-term word map later in the SAME pipeline,
   which has its own unrelated `"file" -> "page"` entry (a real
   simplification for prose that says "your file"). That later pass
   has no idea the word "file" it's looking at was JUST produced by
   step 1 above — it fires on it like any other sentence, silently
   turning EVERY "that file" back into "that page". This means the
   `_PAGE_LIKE_EXTS` / `_DEFAULT_FILE_TERM` distinction (2026-09-12)
   has never actually been effective for any non-page-like file.

Fix: (1) `_FILENAME_RE` now explicitly consumes an optional leading
".", "..", "./", "../" as part of the match, via a negative lookbehind
that still blocks matching mid-identifier. (2) `_humanize_filename`'s
output is now stashed behind its own sentinel placeholder (same
technique already used for URLs) BEFORE the generic word-map pass
runs, and restored verbatim at the very end — so a label this
function already finalized can never be re-processed by a later,
context-blind pass.
"""
from services.business_voice_filter import filter_for_business_owner


def test_leading_dot_filename_no_longer_orphaned():
    # Word-for-word live repro shape.
    raw = (
        "The only .gif_dimension_checker.py in your project is "
        ".gif_dimension_checker.py. It's a GIF dimension/size checker "
        "for Slack..."
    )
    out = filter_for_business_owner(raw)
    assert ".that" not in out.lower()
    assert ".gif_dimension_checker.py" not in out
    assert "that file" in out.lower()


def test_leading_relative_path_filename_no_longer_orphaned():
    for raw in (
        "The only ./validators.py in your project is ./validators.py.",
        "Check ../scripts/scanners/injection_scanner.py for the finding.",
    ):
        out = filter_for_business_owner(raw)
        assert "./" not in out
        assert "../" not in out
        assert "that file" in out.lower()


def test_py_file_says_that_file_not_that_page():
    # The exact class of file from the live repro (validators.py,
    # injection_scanner.py) — a .py extension is not page-like and
    # must land on "that file", never "that page".
    for raw in (
        "validators.py line 88 has a false positive for sql_injection.",
        "Check .agent/skills/plugins/antigravity-awesome-skills-claude/"
        "skills/007/scripts/scanners/injection_scanner.py for the finding.",
        "schema.sql and deploy.sh both need review.",
    ):
        out = filter_for_business_owner(raw)
        assert "that page" not in out.lower(), out
        assert "that file" in out.lower(), out


def test_page_like_extensions_still_say_page_unaffected():
    # Existing behavior for genuinely page-ish files must not regress
    # by this fix.
    assert "your homepage" in filter_for_business_owner(
        "Let me check index.html first."
    ).lower()
    assert "the bottom of your page" in filter_for_business_owner(
        "Let me check Footer.tsx first."
    ).lower()


def test_mid_identifier_still_not_matched():
    # The lookbehind guarding the new leading-dot support must still
    # refuse to match a filename hiding mid-word (unchanged safety
    # property from before this fix).
    out = filter_for_business_owner("myapp.py needs a fix too.")
    assert "myapp.py" not in out
    assert "that file" in out.lower()


def test_fenced_code_block_content_is_never_touched():
    """2026-02 round-4 — confirmed LIVE (founder QA re-test of Bug 2),
    and far more severe than the mangling this file's other tests
    guard against: when a real source-file excerpt is quoted back
    inside a ``` fence (e.g. after read_repo_file), the SAME dev-term
    word map fired on the code itself, not just the prose around it —
    "Path to GIF file" -> "Path to GIF page", an f-string
    `f"File not found: {x}"` -> `f"Page not found: {x}"` (now lying
    about the actual error message the real code raises), and
    `gif_path.stat().st_size` -> `gif_path.stat()a project page` (a
    substitution landing mid-expression with no word boundary at all,
    breaking the Python syntax outright). Fix: fenced code blocks are
    now stashed byte-for-byte BEFORE any other pass in the pipeline
    runs, exactly like this file's existing URL protection."""
    raw = (
        "**`validators.py`** (partial view):\n\n"
        "```\n"
        "def validate_gif(gif_path):\n"
        '    """Path to GIF file"""\n'
        "    if not gif_path.exists():\n"
        '        return False, {"error": f"File not found: {gif_path}"}\n'
        "    size_bytes = gif_path.stat().st_size\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
        "```\n"
    )
    out = filter_for_business_owner(raw)
    # The code itself, byte-for-byte, including every word the
    # generic term map would otherwise have mangled.
    assert "Path to GIF file" in out
    assert 'f"File not found: {gif_path}"' in out
    assert "gif_path.stat().st_size" in out
    assert "except Exception:" in out
    # The prose OUTSIDE the fence is still humanized as before.
    assert "that file" in out.lower()
    assert "validators.py" not in out.split("```")[0]
