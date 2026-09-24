"""
test_business_voice_github_repo_doublesub_2026_09_03.py

User-first wave (2026-09-03) — live-caught bug: a real model reply
containing the adjacent phrase "GitHub repo" was independently
substituted twice (once for "github", once for "repo"), producing a
garbled duplicate: "your Your website's connection website's pages".

Root cause: business_voice_map.json's "github" entry was the only one
with a baked-in "your " prefix (violating the map's own stated
article-free rule), and "github"/"repo" being adjacent words with
separate map entries collided into a run-on. Fix: a longer "github
repo"/"github repository" phrase entry (matched first, per the
existing longest-phrase-first sort) collapses the pair into ONE clean
"website's pages" phrase; the bare "github" entry also dropped its
baked-in "your" to match every other entry's convention.
"""
from services.business_voice_filter import filter_for_business_owner


def test_github_repo_phrase_does_not_double_substitute():
    out = filter_for_business_owner(
        "I can help fix issues in your GitHub repo — just point me to "
        "a problem and I'll go live with a tested solution."
    )
    assert "Your website's connection" not in out
    assert "your your" not in out.lower()
    assert "connection website" not in out.lower()
    assert "website" in out.lower()


def test_github_repository_phrase_collapses_cleanly():
    out = filter_for_business_owner("Connect your GitHub repository so I can scan it.")
    assert out.lower() == "connect your website so i can scan it."


def test_bare_github_no_longer_doubles_the_article():
    out = filter_for_business_owner("Connect your GitHub account first.")
    assert "your your" not in out.lower()
    assert "your website's connection account" in out.lower()


def test_bare_repo_agrees_grammatically_singular():
    # 2026-09-03 (testing_agent live-catch, round 2) — "repo" used to
    # map to the PLURAL-sounding "website's pages", breaking
    # subject-verb agreement in sentences like "no repo is connected".
    out = filter_for_business_owner("Right now, no repo is connected to this chat.")
    assert out == "Right now, no website is connected to this chat."


def test_single_pass_never_re_mangles_its_own_output():
    # The old design applied each map entry as its OWN .sub() call in
    # sequence, so one entry's OUTPUT could collide with a LATER
    # entry's pattern (e.g. a value containing the word "code" would
    # get re-caught by the separate "code" -> "content" rule). The
    # single combined-alternation pass makes this structurally
    # impossible — assert it holds for a value that intentionally
    # contains another map key as a substring ("website's connection"
    # contains no other key, but "repo"/"github" are adjacent here).
    out = filter_for_business_owner("Your github repo has 3 open pull requests.")
    assert "content" not in out.lower()
    assert out == "Your website has 3 open review requests."
