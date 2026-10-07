#!/usr/bin/env python3
"""Run the checkup checks over a local git checkout and print what they report.

Read-only: reads files with `git show`, never writes to the repo and never
calls GitHub or any network service.  Used to count false alarms on real
public repos before a check is trusted (docs/checkup_first/FALSE_ALARM_AUDIT.md).

  python backend/scripts/checkup_false_alarm_audit.py <repo_dir> [<tree-ish>] [--history N] [--json out.json]

`<tree-ish>` may be `HEAD` (default) or `HEAD:some/subdir` to audit one project
inside a monorepo.  Secret values are never printed: history findings carry only
file, commit and credential type.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import checkup_meta as cm                                   # noqa: E402
from services.checkup_orchestrator import run_checkup                     # noqa: E402
from services.codebase_health_core import _is_dockerfile                  # noqa: E402
from services.github_repo_fetch import _MAX_BYTES_PER_FILE, _SCAN_EXTS, _SKIP_DIRS  # noqa: E402


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="ignore").stdout


def _show(repo: str, ref: str, path: str) -> str:
    return _git(repo, "show", f"{ref}/{path}" if ":" in ref else f"{ref}:{path}")


def _scannable(path: str) -> bool:
    low = path.lower()
    if any(part in _SKIP_DIRS for part in path.split("/")):
        return False
    return (any(low.endswith(e) for e in _SCAN_EXTS) or low.endswith(("requirements.txt", "package.json"))
            or _is_dockerfile(low))


def read_files(repo: str, ref: str, paths: list[str]) -> dict[str, str]:
    """The same file selection the product's text cache makes (extensions, skipped dirs, size cap)."""
    tc = {}
    for p in filter(_scannable, paths):
        text = _show(repo, ref, p)
        if text and len(text) <= _MAX_BYTES_PER_FILE:
            tc[p] = text
    return tc


def _commit_files(repo: str, ref: str, sha: str) -> list[dict]:
    scope = ["--", ref.split(":", 1)[1]] if ":" in ref else []
    patch = _git(repo, "show", "--format=", "--patch", "--unified=0", sha, *scope)
    files = []
    for block in re.split(r"^diff --git ", patch, flags=re.M)[1:]:
        m = re.search(r"^\+\+\+ b/(.+)$", block, re.M)
        if m:
            files.append({"filename": m.group(1), "patch": block})
    return files


def history_findings(repo: str, ref: str, n: int) -> list[dict]:
    try:
        from services.checkup_history import secrets_in_patch
    except ImportError:                       # secrets checks not in this checkout
        return []
    out = []
    for sha in _git(repo, "log", f"-{n}", "--format=%H", ref.split(":")[0]).split():
        out += [{"commit": sha[:7], "path": p, "type": k}
                for p, k in secrets_in_patch(_commit_files(repo, ref, sha))]
    return out


def build_cache(repo: str, ref: str, history_n: int) -> tuple[dict[str, str], int]:
    paths = [p for p in _git(repo, "ls-tree", "-r", "--name-only", ref).splitlines() if p]
    env_paths = [p for p in paths if re.search(r"(?:^|/)\.env(?:\.[\w-]+)?$", p)
                 and not p.endswith((".example", ".sample", ".template"))][:10]
    extra = {name: _show(repo, ref, name) for name in [".gitignore", *env_paths] if name in paths}
    tc = read_files(repo, ref, paths)
    kw = {}
    if "history" in inspect.signature(cm.with_meta).parameters:     # secrets checks present
        kw["history"] = history_findings(repo, ref, history_n)
    return cm.with_meta(tc, paths, extra, **kw), len(paths)


def summarize(res: dict, files_total: int) -> dict:
    return {
        "stack": res["stack"]["label"], "repo_files": files_total,
        "items": [{"rule": i["rule_id"], "severity": i["severity"], "confidence": i.get("confidence"),
                   "file": i["evidence"]["file"], "line": i["evidence"]["line"],
                   "detail": i["evidence"]["detail"]} for i in res["items"]],
        "unverified": [{"check": u["check"], "stage": u["stage"], "reason": u["reason"]} for u in res["unverified"]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("ref", nargs="?", default="HEAD")
    ap.add_argument("--history", type=int, default=20)
    ap.add_argument("--json")
    args = ap.parse_args()
    tc, total = build_cache(args.repo, args.ref, args.history)
    out = summarize(run_checkup(tc, include_scanners=False), total)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1)
    print(f"{out['stack']} | repo files {total} | items {len(out['items'])} | unverified {len(out['unverified'])}")
    for (rule, sev), n in Counter((i["rule"], i["severity"]) for i in out["items"]).most_common():
        print(f"  {n:4d}  {sev:8s} {rule}")
    for i in out["items"]:
        if i["severity"] in ("critical", "high"):
            print(f"  -> {i['rule']} {i['file']}:{i['line']}  {i['detail'][:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
