"""
services/checkup_checks_secrets.py — Check Library v2, layer 7 (secrets
and version control): L7.1 env files, L7.2 git history.

Evidence names the file, the variable NAME or credential TYPE — never the
value (S8).  A check we couldn't run raises MetaUnavailable → unverified.
"""
from __future__ import annotations

import fnmatch
import re

from services.checkup_common import _files, _is_vendored, _item
from services.checkup_meta import history_findings, meta_file, repo_paths
from services.checkup_secret_patterns import is_env_file, label_of, secret_env_names

_USES_ENV = re.compile(r"process\.env\.|import\.meta\.env\.|os\.environ|os\.getenv|dotenv|load_dotenv")


def _committed_env_items(tc: dict[str, str]) -> list[dict]:
    out = []
    for path in repo_paths(tc):
        if not is_env_file(path) or _is_vendored(path):
            continue
        text = meta_file(tc, path)
        names = secret_env_names(text) if text else []
        if not names:
            continue
        out.append(_item(
            scanner="secrets", rule_id="env_file_committed", severity="critical",
            title="Your private passwords and keys are saved in your code",
            impact="Anyone who can see the code, now or in future, can use these keys to run up your bills or read your data.",
            file=path, line=1,
            detail=f"{len(names)} secret setting(s) are committed: {', '.join(sorted(names)[:8])} (values not shown)",
            fix_hint="Remove the file from the repo, add it to .gitignore, then replace every key it contained."))
    return out


def _ignores_env(gitignore: str | None) -> bool:
    """Does any non-negated .gitignore pattern cover a root `.env` file?"""
    for raw in (gitignore or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        pat = line.lstrip("/")
        pat = pat[3:] if pat.startswith("**/") else pat
        if fnmatch.fnmatch(".env", pat.rstrip("/")):
            return True
    return False


def _project_uses_env(tc: dict[str, str]) -> bool:
    return any(_USES_ENV.search(text) for _p, text in _files(tc))


def _env_not_ignored_item(tc: dict[str, str]) -> list[dict]:
    gitignore = meta_file(tc, ".gitignore")
    if _ignores_env(gitignore) or not _project_uses_env(tc):
        return []
    return [_item(
        scanner="secrets", rule_id="env_not_gitignored", severity="medium",
        title="One careless save could publish your private keys",
        impact="Your project reads secret settings from a .env file, but nothing stops that file from being uploaded with your code.",
        file=".gitignore" if gitignore is not None else "(no .gitignore)", line=1,
        detail=".env is not covered by .gitignore" if gitignore is not None else "the repo has no .gitignore",
        fix_hint="Add a line `.env` to .gitignore.")]


def check_env_files(tc: dict[str, str]) -> list[dict]:
    """L7.1 — committed env file with real secrets (critical), or env file
    not protected by .gitignore (medium: nothing leaked yet)."""
    return _committed_env_items(tc) + _env_not_ignored_item(tc)


def check_secrets_in_history(tc: dict[str, str]) -> list[dict]:
    """L7.2 — credentials added in the newest commits (still dangerous if
    since deleted)."""
    out = []
    for f in history_findings(tc):
        label = label_of(f["type"])
        item = _item(
            scanner="secrets", rule_id="secret_in_git_history", severity="critical",
            title=f"A {label} was saved in your code history",
            impact="Deleting it from the latest code doesn't help: anyone who can see the history can still copy it.",
            file=f["path"], line=0,
            detail=f"{label} added in commit {f['commit']} (value not shown; checked the newest commits only)",
            fix_hint="Treat the key as leaked: create a new one, switch your app to it, and disable the old one.")
        out.append({**item, "id": f"secrets::{f['path']}:{f['commit']}:{f['type']}"})
    return out


CHECKS = [
    ("safe", "env_files", check_env_files),
    ("safe", "secrets_in_history", check_secrets_in_history),
]
