"""Tests for the GitHubBox / github.dev sandbox links.

Covers services.github_sandbox_links and the hook in
services.repo_context._wrap that puts the line into Ora's repo
context. No network, no auth, no new dependency.
"""
from services.github_sandbox_links import (
    GitHubRepoRef,
    append_sandbox_links,
    has_package_json_in_tree,
    parse_github_repo_url,
    sandbox_links_line,
)
from services.repo_context import _wrap


def test_react_js_shows_both_links_codesandbox_first():
    line = sandbox_links_line("https://github.com/facebook/react", True)
    assert line == (
        "Open it: [Run in CodeSandbox](https://githubbox.com/facebook/react)"
        " \u00b7 [Open in VS Code (browser)](https://github.dev/facebook/react)"
    )


def test_linux_no_package_json_shows_vscode_only():
    line = sandbox_links_line("https://github.com/torvalds/linux", False)
    assert line == "Open it: [Open in VS Code (browser)](https://github.dev/torvalds/linux)"
    assert "githubbox.com" not in line


def test_tree_branch_path_preserved_on_both_links():
    line = sandbox_links_line("https://github.com/AuremHQ/Aurem/tree/main/frontend", True)
    assert "githubbox.com/AuremHQ/Aurem/tree/main/frontend" in line
    assert "github.dev/AuremHQ/Aurem/tree/main/frontend" in line


def test_blob_path_preserved():
    ref = parse_github_repo_url("https://github.com/facebook/react/blob/main/README.md")
    assert ref == GitHubRepoRef("facebook", "react", "/blob/main/README.md")


def test_non_github_url_gives_no_links():
    assert sandbox_links_line("https://example.com/foo/bar", True) == ""
    assert sandbox_links_line("https://githubbox.com/facebook/react", True) == ""
    assert sandbox_links_line("https://github.dev/facebook/react", True) == ""
    assert append_sandbox_links("Hello.", "https://example.com/x", True) == "Hello."


def test_unknown_package_json_gives_vscode_only():
    line = sandbox_links_line("https://github.com/facebook/react", None)
    assert line == "Open it: [Open in VS Code (browser)](https://github.dev/facebook/react)"
    assert "githubbox.com" not in line


def test_private_repo_vscode_only_unless_js_confirmed():
    assert "githubbox.com" not in sandbox_links_line("https://github.com/acme/secret", None, True)
    assert "githubbox.com" in sandbox_links_line("https://github.com/acme/secret", True, True)
    assert "github.dev/acme/secret" in sandbox_links_line("https://github.com/acme/secret", None, True)


def test_append_is_idempotent_and_keeps_reply():
    reply = "React is a UI library."
    out = append_sandbox_links(reply, "https://github.com/facebook/react", True)
    assert out.startswith("React is a UI library.\n\nOpen it: ")
    assert "githubbox.com/facebook/react" in out
    assert append_sandbox_links(out, "https://github.com/facebook/react", True) == out
    assert append_sandbox_links("", "https://github.com/facebook/react", True).startswith("Open it: ")


def test_parse_strips_git_suffix_query_and_punctuation():
    assert parse_github_repo_url("Check https://github.com/facebook/react.git!") == GitHubRepoRef("facebook", "react")
    assert parse_github_repo_url("https://github.com/torvalds/linux?tab=readme") == GitHubRepoRef("torvalds", "linux")
    assert parse_github_repo_url("https://github.com/facebook/react/issues/1") == GitHubRepoRef("facebook", "react")


def test_has_package_json_in_tree():
    assert has_package_json_in_tree([{"path": "README.md"}, {"path": "package.json"}]) is True
    assert has_package_json_in_tree([{"path": "README.md"}, {"path": "src/index.js"}]) is False
    assert has_package_json_in_tree(None) is None
    assert has_package_json_in_tree([{"path": "packages/app/package.json"}]) is False
    assert has_package_json_in_tree([{"path": "frontend/package.json"}], "frontend") is True


def test_wrap_includes_sandbox_line_for_js_repo():
    out = _wrap("facebook", "react", "main", "", "", "", True)
    assert "Open it: [Run in CodeSandbox](https://githubbox.com/facebook/react)" in out
    assert "github.dev/facebook/react" in out


def test_wrap_vscode_only_when_package_json_unknown_or_absent():
    no_pkg = _wrap("torvalds", "linux", "master", "", "", "", False)
    assert "githubbox.com" not in no_pkg
    assert "github.dev/torvalds/linux" in no_pkg
    unknown = _wrap("acme", "widgets", "main", "", "", "")
    assert "githubbox.com" not in unknown
    assert "github.dev/acme/widgets" in unknown
