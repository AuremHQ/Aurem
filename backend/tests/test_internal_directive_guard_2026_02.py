"""
tests/test_internal_directive_guard_2026_02.py

Regression lock for a live-repro'd bug this same engagement (founder
QA report): asked to check exec_usage in injection_scanner.py at a
path that doesn't exist in the target project. The system correctly
attempted a real file read and correctly reported "not found" (no
hallucination) — but the final user-facing response also included,
verbatim, an internal self-directed instruction meant to steer the
MODEL's own next tool call (services/local_tools.py's read_repo_file
404 error template): "STOP guessing paths. Your next tool call MUST
be list_repo_files ... do not cite any file paths — until you have
called list_repo_files and seen the actual layout."

Also confirmed (Bug 4 in the same report): this did NOT reproduce on
a second, similar not-found-adjacent case — i.e. it's intermittent
depending on whether the model pastes the raw tool-result verbatim or
properly acts on it first. A deterministic output-side strip is the
fix for both the case that leaked and the case that didn't (this
guard is a no-op when the text isn't there).
"""
from services.internal_directive_guard import strip_leaked_tool_directives


def test_leaked_file_not_found_directive_is_stripped():
    # Word-for-word live repro shape.
    leaked = (
        "I checked and the file does not exist at that path in this "
        "project. STOP guessing paths. Your next tool call MUST be "
        "list_repo_files (e.g. **/auth*.py, **/*router*.py) to DISCOVER "
        "the real paths in this repo. Do not write a plan, do not "
        "produce a handoff brief, do not cite any file paths — until "
        "you have called list_repo_files and seen the actual layout. "
        "Let me know if you have a different path in mind."
    )
    out = strip_leaked_tool_directives(leaked)
    assert "STOP guessing paths" not in out
    assert "list_repo_files" not in out
    assert "do not cite any file paths" not in out
    # The legitimate, informative parts of the same reply survive.
    assert "does not exist at that path" in out
    assert "Let me know if you have a different path in mind" in out


def test_leaked_github_revoked_internal_sentences_stripped_message_kept():
    leaked = (
        "GitHub access to acme/repo was revoked. STOP calling repo "
        "tools — they will all fail the same way until access is "
        "restored. Tell the user clearly: 'GitHub access to acme/repo "
        "was revoked — reconnect this repo from the project settings "
        "to keep working.' Do not guess at file paths or blame an "
        "unrelated API."
    )
    out = strip_leaked_tool_directives(leaked)
    assert "STOP calling repo tools" not in out
    assert "Do not guess at file paths or blame an unrelated API" not in out
    # The real end-user message embedded in the same template survives.
    assert "reconnect this repo from the project settings" in out


def test_normal_responses_are_never_touched():
    # Bug 4's "did not reproduce on a second case" — must be a true
    # no-op on ordinary text, including text that legitimately talks
    # about stopping/paths/repos in unrelated ways.
    for text in (
        "I read validators.py line 88 and it's a false positive.",
        "You should stop and check the repo settings before continuing.",
        "",
        None,
    ):
        assert strip_leaked_tool_directives(text) == text
