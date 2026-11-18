"""One-click sandbox links for GitHub repos shared with Ora.

When a user shares a GitHub repo URL, Ora's reply ends with one short line
that lets the user open, explore and run the repo in their browser - no
cloning, no setup. The links are plain text in the reply; nothing is
auto-opened and Ora itself still cannot run code.

Link rules (never guess):

* GitHubBox (githubbox.com, which redirects to CodeSandbox) is only
  offered when the repo is confirmed to be a JavaScript/Node project,
  i.e. a package.json is present in the fetched repo tree. GitHubBox
  only really works for JS/Node projects.
* The VS Code (browser) link (github.dev) is offered for every repo,
  including private repos (the user signs in there) and repos whose
  package.json presence cannot be determined (fetch failed, private
  repo, API error).
* A branch/path from the shared URL (/tree/<branch>/<path> or
  /blob/<branch>/<path>) is preserved on both links.
* Non-GitHub URLs never produce sandbox links.

This module is pure (stdlib only, no network, no auth): it parses a
GitHub URL, decides which links apply from a package.json flag that
the caller already determined from the repo fetch, and formats or
appends the reply line. Callers must not invent the flag - pass
None when package.json presence is unknown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Union

__all__ = [
    "GitHubRepoRef",
    "parse_github_repo_url",
    "has_package_json_in_tree",
    "sandbox_links_line",
    "append_sandbox_links",
]

# Middle dot used to separate the two links, exactly as shown to users.
_SEPARATOR = " \u00b7 "

_SEGMENT = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
# A GitHub URL in free text: scheme optional, www optional, then
# github.com/<owner>/<repo> with an optional /tree/... or /blob/... tail.
# Anything else on the host (api.github.com, gist.github.com,
# githubbox.com, github.dev, ...) must not match.
_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>" + _SEGMENT + r")/"
    r"(?P<repo>" + _SEGMENT + r")"
    r"(?P<tail>/[^\s\"'<>\\)\]]*)?",
    re.IGNORECASE,
)
_TRAILING_PUNCT = ".,;:!?)\]}'\""


@dataclass(frozen=True)
class GitHubRepoRef:
    """A parsed github.com repo URL, with any /tree or /blob tail kept."""

    owner: str
    repo: str
    suffix: str = ""

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}{self.suffix}"

    @property
    def githubbox_url(self) -> str:
        return f"https://githubbox.com/{self.owner}/{self.repo}{self.suffix}"

    @property
    def github_dev_url(self) -> str:
        return f"https://github.dev/{self.owner}/{self.repo}{self.suffix}"


def _clean_tail(tail: str) -> str:
    """Keep only a /tree/... or /blob/... tail; drop anything else."""
    if not tail:
        return ""
    # Strip a query string or fragment, then sentence punctuation.
    tail = tail.split("?", 1)[0].split("#", 1)[0]
    tail = tail.rstrip(_TRAILING_PUNCT).rstrip("/")
    parts = [p for p in tail.split("/") if p]
    if len(parts) < 2 or parts[0] not in ("tree", "blob"):
        return ""
    return "/" + "/".join(parts)


def parse_github_repo_url(text: Optional[str]) -> Optional[GitHubRepoRef]:
    """Return the first github.com repo URL found in ``text``, or None.

    Accepts a bare URL or a URL inside a longer message. Only the
    github.com host counts - githubbox.com, github.dev, api.github.com
    and any other host return None. A trailing ``.git``, query string,
    fragment or sentence punctuation is stripped. Only a /tree or /blob
    tail is kept; other tails (/issues, /pulls, ...) fall back to the
    repo root so the sandbox links still open the repo.
    """
    if not text:
        return None
    match = _URL_RE.search(text)
    if match is None:
        return None
    repo = match.group("repo")
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not repo:
        return None
    return GitHubRepoRef(
        owner=match.group("owner"),
        repo=repo,
        suffix=_clean_tail(match.group("tail") or ""),
    )


def _tree_paths(tree: object) -> Optional[list]:
    """Normalise a repo tree into a list of paths, or None if unknown."""
    if tree is None:
        return None
    if isinstance(tree, dict):
        for key in ("tree", "paths", "files"):
            if key in tree:
                return _tree_paths(tree[key])
        return [str(k) for k in tree.keys()]
    if isinstance(tree, str):
        return [line.strip() for line in tree.splitlines() if line.strip()]
    if isinstance(tree, Iterable):
        paths: list = []
        for entry in tree:
            if isinstance(entry, dict):
                path = entry.get("path")
                if isinstance(path, str):
                    paths.append(path)
            elif isinstance(entry, str):
                paths.append(entry)
        return paths
    return None


def has_package_json_in_tree(tree: object, path: Optional[str] = None) -> Optional[bool]:
    """True if the fetched tree contains a root package.json.

    Returns None when the tree is unknown (no fetch result), False
    when the tree is known and has no package.json, and True only when
    a package.json is actually present. A package.json in a nested
    folder does not count, unless ``path`` names that folder (for a
    /tree/<branch>/<path> link, the folder is the project root there).
    """
    paths = _tree_paths(tree)
    if paths is None:
        return None
    wanted = {"package.json"}
    if path:
        wanted.add(path.strip("/") + "/package.json")
    return any(p in wanted for p in paths)


def sandbox_links_line(
    ref_or_url: Union[GitHubRepoRef, str, None],
    has_package_json: Optional[bool] = None,
    is_private: bool = False,
) -> str:
    """Build the one-line sandbox footer for a repo, or "" if none applies.

    ``has_package_json`` must be True only when a package.json was
    actually seen in the fetched repo tree. None (unknown - fetch
    failed, private repo, API error) and False both give the VS Code
    link only. A private repo still gets the VS Code link, and gets
    the CodeSandbox link too when JS is confirmed (has_package_json
    is True). ``is_private`` is accepted for callers that know it;
    it never adds a CodeSandbox link on its own.
    """
    if isinstance(ref_or_url, GitHubRepoRef):
        ref = ref_or_url
    else:
        ref = parse_github_repo_url(ref_or_url)
    if ref is None:
        return ""
    vscode_link = f"[Open in VS Code (browser)]({ref.github_dev_url})"
    if has_package_json is True:
        codesandbox_link = f"[Run in CodeSandbox]({ref.githubbox_url})"
        return f"Open it: {codesandbox_link}{_SEPARATOR}{vscode_link}"
    return f"Open it: {vscode_link}"


def append_sandbox_links(
    reply: Optional[str],
    ref_or_url: Union[GitHubRepoRef, str, None],
    has_package_json: Optional[bool] = None,
    is_private: bool = False,
) -> str:
    """Append the sandbox line to Ora's reply, after its normal answer.

    Returns the reply unchanged when there is no GitHub repo URL,
    when the reply already contains sandbox links, or when the
    reply is empty (in which case just the line is returned, so the
    user still gets the links). Idempotent: appending twice adds the
    line only once.
    """
    line = sandbox_links_line(ref_or_url, has_package_json, is_private)
    if not line:
        return reply or ""
    if reply and ("githubbox.com/" in reply or "github.dev/" in reply):
        return reply
    if not reply or not reply.strip():
        return line
    return reply.rstrip() + "\n\n" + line
