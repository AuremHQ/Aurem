# SSRF + repo-path traversal guard for GitHub / OSV fetches (Aurem fix #11).
# codebase_health (health scan) and repo_indexing fetch repo content via
# services.github_repo_fetch, services.repo_context and
# services.github_api_writer, and CVE data via services.osv_client. Those
# fetchers build api.github.com / api.osv.dev URLs from user-supplied
# values (github_owner, github_repo, branch, repo-relative file paths,
# CVE ids) and, until this fix, followed HTTP redirects to any host
# while carrying the caller's GitHub token.
# Rules: (1) URL allowlist -- only https://api.github.com/... and
# https://api.osv.dev/... may be fetched. (2) Redirects -- the GitHub /
# OSV clients are created with follow_redirects=False, so a 3xx to an
# untrusted host is never followed. (3) Repo paths -- a repo-relative
# path is the repo's filesystem path, repo root as base directory:
# validate_repo_path() rejects absolute paths, '..' segments, backslash
# and NUL, and URL building quotes every segment so '?', '#', '%2e%2e'
# cannot inject a query/fragment or a second segment. (4) owner/repo
# are single segments; branch is one or more segments (feature branches
# contain '/'), each validated the same way. (5) resolve_inside() is
# the same check for a real local path (resolve + is_relative_to).
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlencode, urlparse

GITHUB_API_HOST = "api.github.com"
OSV_API_HOST = "api.osv.dev"
GITHUB_API_BASE = f"https://{GITHUB_API_HOST}"
OSV_API_BASE = f"https://{OSV_API_HOST}"

_GITHUB_HOSTS = frozenset({GITHUB_API_HOST})
_OSV_HOSTS = frozenset({OSV_API_HOST})

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VULN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class UnsafeFetchError(ValueError):
    pass


class UnsafePathError(ValueError):
    pass


def validate_url(url, allowed_hosts, allowed_path_prefixes=()):
    # SSRF allowlist: https only, no userinfo, no non-standard port,
    # host exactly in the allowlist, path under an allowed prefix.
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise UnsafeFetchError(f"refusing non-https fetch: {url!r}")
    if parsed.username or parsed.password:
        raise UnsafeFetchError(f"refusing URL with credentials: {url!r}")
    if parsed.port not in (None, 443):
        raise UnsafeFetchError(f"refusing URL with non-standard port: {url!r}")
    if host not in allowed_hosts:
        raise UnsafeFetchError(f"refusing fetch to untrusted host {host!r}")
    if allowed_path_prefixes and not any(
        parsed.path.startswith(prefix) for prefix in allowed_path_prefixes
    ):
        raise UnsafeFetchError(f"refusing fetch outside allowed paths: {url!r}")
    return url


def validate_github_url(url):
    return validate_url(url, _GITHUB_HOSTS, ("/repos/",))


def validate_osv_url(url):
    return validate_url(url, _OSV_HOSTS, ("/v1/",))


def validate_owner_repo(owner, repo):
    # owner / repo are single URL path segments: no '/', '..', '?', '#'.
    for value, label in ((owner, "owner"), (repo, "repo")):
        if not isinstance(value, str) or not value:
            raise UnsafePathError(f"invalid github {label}: {value!r}")
        if value in (".", "..") or ".." in value:
            raise UnsafePathError(f"invalid github {label}: {value!r}")
        if not _SEGMENT_RE.match(value):
            raise UnsafePathError(f"invalid github {label}: {value!r}")
    return owner, repo


def validate_branch(branch):
    # A branch may contain '/' (feature/foo) but no '..', empty segment,
    # backslash, NUL or URL metacharacters.
    if not isinstance(branch, str) or not branch:
        raise UnsafePathError(f"invalid github branch: {branch!r}")
    if "\\" in branch or "\x00" in branch:
        raise UnsafePathError(f"invalid github branch: {branch!r}")
    # Git branch names may contain most printable characters; quoting
    # each segment makes them safe in a URL path, so only empty / dot
    # segments, backslash and control characters are refused.
    for segment in branch.split("/"):
        if segment in ("", ".", ".."):
            raise UnsafePathError(f"invalid github branch: {branch!r}")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in segment):
            raise UnsafePathError(f"invalid github branch: {branch!r}")
    return branch


def validate_vuln_id(vuln_id):
    if not isinstance(vuln_id, str) or not _VULN_ID_RE.match(vuln_id):
        raise UnsafePathError(f"invalid vulnerability id: {vuln_id!r}")
    return vuln_id


def validate_repo_path(path):
    # Repo-relative path, repo root as base directory: resolve it the
    # same way a local path is resolved against a base directory and
    # reject it if it would escape the repo root. Percent-encoded
    # traversal ('%2e%2e', '%2f') is decoded first so it cannot hide.
    if not isinstance(path, str) or not path.strip():
        raise UnsafePathError("empty repo path")
    decoded = unquote(path)
    if "\x00" in decoded:
        raise UnsafePathError(f"invalid repo path: {path!r}")
    cleaned = decoded.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/")
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    if not cleaned or cleaned == ".":
        raise UnsafePathError(f"invalid repo path: {path!r}")
    parts = PurePosixPath(cleaned).parts
    if PurePosixPath(cleaned).is_absolute():
        raise UnsafePathError(f"invalid repo path: {path!r}")
    if any(part in ("..", ".") for part in parts):
        raise UnsafePathError(f"repo path escapes repo root: {path!r}")
    return "/".join(parts)


def quote_repo_path(path):
    return "/".join(quote(seg, safe="") for seg in validate_repo_path(path).split("/"))


def quote_branch(branch):
    return "/".join(quote(seg, safe="") for seg in validate_branch(branch).split("/"))


def github_api_url(owner, repo, *segments, query=None):
    # Build an api.github.com URL from validated parts only; every
    # segment is quoted, so no part can inject '/', '?' or '#'.
    validate_owner_repo(owner, repo)
    path = "/".join(quote(str(seg), safe="") for seg in segments)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    if path:
        url = f"{url}/{path}"
    if query:
        url = f"{url}?{query}"
    return validate_github_url(url)


def github_contents_url(owner, repo, path, branch=None):
    return github_api_url(
        owner,
        repo,
        "contents",
        *validate_repo_path(path).split("/"),
        query=urlencode({"ref": validate_branch(branch)}) if branch else None,
    )


def github_tree_url(owner, repo, branch):
    return github_api_url(
        owner,
        repo,
        "git",
        "trees",
        *validate_branch(branch).split("/"),
        query="recursive=1",
    )


def osv_vuln_url(vuln_id):
    url = f"{OSV_API_BASE}/v1/vulns/{quote(validate_vuln_id(vuln_id), safe='')}"
    return validate_osv_url(url)


def resolve_inside(base_dir, user_path):
    # Local-filesystem equivalent of validate_repo_path(): resolve the
    # user-supplied path against base_dir and reject it if the resolved
    # path escapes the base directory (realpath + is_relative_to).
    base = Path(base_dir).resolve()
    target = (base / user_path).resolve()
    if not target.is_relative_to(base):
        raise UnsafePathError(f"path escapes base directory: {user_path!r}")
    return target


def validate_git_sha(sha):
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", sha):
        raise UnsafePathError(f"invalid git sha: {sha!r}")
    return sha.lower()


def github_ref_url(owner, repo, branch):
    return github_api_url(owner, repo, "git", "refs", "heads", *validate_branch(branch).split("/"))


def github_commit_url(owner, repo, sha):
    return github_api_url(owner, repo, "commits", validate_git_sha(sha))


def github_git_commit_url(owner, repo, sha):
    return github_api_url(owner, repo, "git", "commits", validate_git_sha(sha))
