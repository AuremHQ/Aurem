"""
services/bot_pr_reaper.py — Bug 1 fix (2026-09-17): runaway duplicate
bot PRs. 389 open PRs accumulated on one connected repo because the
old branch-naming scheme minted a fresh timestamped branch (and
therefore a fresh PR) on every run of the same fix, with nothing
closing superseded ones.

The primary fix (deterministic branch names + check-before-create in
`finding_fix_applier.py` / `loop_safety.py`) stops NEW duplicates from
being created. This module is the safety net the report explicitly
asked for: a scheduled reaper that catches anything that slips
through — most importantly the existing backlog minted under the old
scheme, whose branch names still carry a unique trailing timestamp
and therefore never merge/collide on their own.

Grouping strategy: an Aurem-authored PR is identified by our own
"**AUREM auto-fix**" marker in the PR body (portable across both App
and PAT auth — doesn't depend on `pr.user.login`, which varies with
auth method). Duplicates are grouped by branch name with any trailing
`-<digits>` (old timestamp suffix) stripped, so
`aurem/fix-todo_comment-1758100000` and
`aurem/fix-todo_comment-1758100777` collapse into the same group and
only the newest PR in each group survives; older ones are closed with
an explanatory comment, never silently.
"""
from __future__ import annotations

import logging
import re
import time

from services.loop_safety import close_pr, add_pr_comment, github_request_with_retry

logger = logging.getLogger("bot_pr_reaper")

AUREM_FIX_MARKER = "AUREM auto-fix"
# 2026-09-18 fix (Bug 1 round 2) — the reaper's OWN safety net had the
# same "only covers one call site" gap as the primary fix: it only
# ever matched `finding_fix_applier.py`'s "**AUREM auto-fix**" body
# marker, so it silently never even SAW the reported Visibility Kit /
# ship-via-PR / visual-bugfix duplicates in `_list_open_bot_prs` —
# those 3 call sites use their own body wording. Now matches any of
# the 4 known Aurem-bot PR markers, so a legacy backlog from ANY of
# them gets swept, not just the one this reaper was originally built
# to clean up.
AUREM_BOT_MARKERS = (
    AUREM_FIX_MARKER,                          # finding_fix_applier.py
    "Visibility Kit (",                        # services/visibility/apply.py
    "Opened by ORA (Loop ",                    # loop_engine.py ship-via-PR
    "AUREM's Visual Bug-Fix pipeline",          # services/visual_bugfix_pr.py
)
# Strip a trailing "-<digits>" segment (the old per-run timestamp) so
# legacy duplicates collapse into one dedup group.
_TRAILING_TS_RE = re.compile(r"-\d{9,}$")


def _dedup_group_key(branch: str) -> str:
    return _TRAILING_TS_RE.sub("", branch or "")


async def _list_open_bot_prs(*, owner: str, repo: str, token: str) -> list[dict]:
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-pr-reaper",
    }
    out: list[dict] = []
    try:
        r = await github_request_with_retry(
            "GET", f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers=headers, params={"state": "open", "per_page": 100},
        )
        if r.status_code == 200:
            for pr in (r.json() or []):
                if any(m in (pr.get("body") or "") for m in AUREM_BOT_MARKERS):
                    out.append(pr)
        else:
            logger.warning("_list_open_bot_prs(%s/%s) HTTP %s", owner, repo, r.status_code)
    except Exception as e:                                   # noqa: BLE001
        logger.warning("_list_open_bot_prs(%s/%s) failed: %r", owner, repo, e)
    return out


async def reap_duplicate_bot_prs_for_repo(
    *, owner: str, repo: str, token: str,
) -> dict:
    """Closes superseded Aurem-fix PRs for one repo, keeping the
    newest per dedup group. Returns
    {"ok", "open_bot_prs", "groups", "closed": [pr_number, ...], "errors": [...]}.
    Never raises — a failure here must not affect any other repo's
    sweep or the fix pipeline itself."""
    result = {"ok": True, "open_bot_prs": 0, "groups": 0,
               "closed": [], "errors": []}
    try:
        prs = await _list_open_bot_prs(owner=owner, repo=repo, token=token)
        result["open_bot_prs"] = len(prs)
        groups: dict[str, list[dict]] = {}
        for pr in prs:
            key = _dedup_group_key((pr.get("head") or {}).get("ref") or "")
            groups.setdefault(key, []).append(pr)
        result["groups"] = len(groups)
        for key, group_prs in groups.items():
            if len(group_prs) <= 1:
                continue
            # Keep the highest PR number (= most recently opened);
            # close the rest.
            group_prs.sort(key=lambda p: p.get("number") or 0, reverse=True)
            keeper, dupes = group_prs[0], group_prs[1:]
            for dupe in dupes:
                try:
                    ok, err = await close_pr(
                        owner=owner, repo=repo,
                        pr_number=dupe["number"], token=token,
                    )
                    if ok:
                        await add_pr_comment(
                            owner=owner, repo=repo,
                            pr_number=dupe["number"],
                            body=(
                                f"Closing — superseded by #{keeper['number']} "
                                f"(same fix, re-run). Automated cleanup, "
                                f"see standing fix for runaway duplicate PRs."
                            ),
                            token=token,
                        )
                        result["closed"].append(dupe["number"])
                    else:
                        result["errors"].append(
                            {"pr": dupe["number"], "error": err})
                except Exception as e:                       # noqa: BLE001
                    result["errors"].append(
                        {"pr": dupe.get("number"), "error": repr(e)[:200]})
    except Exception as e:                                   # noqa: BLE001
        result["ok"] = False
        result["errors"].append({"error": repr(e)[:200]})
    return result


async def reap_duplicate_bot_prs(db) -> dict:
    """Sweeps every connected project with GitHub linkage. Returns
    {"ok", "checked_repos", "total_open_bot_prs", "total_closed",
    "per_repo": [...]}. Best-effort per repo — one repo's failure
    never aborts the sweep for the others."""
    from services.pat_vault import get_repo_token_or_error

    per_repo: list[dict] = []
    total_open = 0
    total_closed = 0
    checked_repos = 0

    seen_repos: set[tuple[str, str]] = set()
    projects = await db.cto_projects.find(
        {"github_owner": {"$nin": [None, ""]},
         "github_repo":  {"$nin": [None, ""]}},
        {"_id": 0, "project_id": 1, "github_owner": 1, "github_repo": 1,
         "github_branch": 1, "auth_method": 1, "installation_id": 1,
         "user_id": 1, "github_token": 1},
    ).to_list(2000)

    for proj in projects:
        owner, repo = proj.get("github_owner"), proj.get("github_repo")
        if not (owner and repo) or (owner, repo) in seen_repos:
            continue
        seen_repos.add((owner, repo))
        token, err, _detail = await get_repo_token_or_error(proj)
        if not token:
            per_repo.append({"owner": owner, "repo": repo, "skipped": err})
            continue
        checked_repos += 1
        res = await reap_duplicate_bot_prs_for_repo(
            owner=owner, repo=repo, token=token,
        )
        total_open += res["open_bot_prs"]
        total_closed += len(res["closed"])
        per_repo.append({"owner": owner, "repo": repo, **res})

    return {
        "ok": True, "checked_repos": checked_repos,
        "total_open_bot_prs": total_open, "total_closed": total_closed,
        "per_repo": per_repo,
    }


BOT_OPEN_PRS_ALERT_THRESHOLD = 10


async def schedule_bot_pr_reaper_cron(db_getter, interval_s: int = 3600) -> None:
    """Background cron — hourly sweep + `bot_open_prs_count` metric
    with a founder alert if it climbs above
    `BOT_OPEN_PRS_ALERT_THRESHOLD` (report's explicit ask: "so this
    can't silently regrow to 400"). Same `db_getter` scheduler shape
    as `schedule_github_installation_reconcile`."""
    import asyncio
    await asyncio.sleep(300)  # let App config + boot settle first
    while True:
        try:
            db = db_getter()
            if db is not None:
                result = await reap_duplicate_bot_prs(db)
                logger.info(
                    "[bot-pr-reaper] checked_repos=%s open_bot_prs=%s closed=%s",
                    result["checked_repos"], result["total_open_bot_prs"],
                    result["total_closed"],
                )
                try:
                    await db.admin_metrics.update_one(
                        {"_id": "bot_open_prs_count"},
                        {"$set": {"value": result["total_open_bot_prs"],
                                  "updated_at": time.time()}},
                        upsert=True,
                    )
                except Exception as e:                        # noqa: BLE001
                    logger.warning("[bot-pr-reaper] metric write failed: %r", e)
                if result["total_open_bot_prs"] > BOT_OPEN_PRS_ALERT_THRESHOLD:
                    from services.founder_alerts import send_founder_alert
                    await send_founder_alert(
                        db,
                        source_key="bot_open_prs_count",
                        title="Bot-opened PRs climbing",
                        detail=(
                            f"{result['total_open_bot_prs']} open Aurem-fix "
                            f"PRs across {result['checked_repos']} repo(s) — "
                            f"above the {BOT_OPEN_PRS_ALERT_THRESHOLD} "
                            f"threshold. Reaper closed {result['total_closed']} "
                            f"duplicate(s) this pass; the remainder are "
                            f"legitimately distinct fixes awaiting review."
                        ),
                        level="warning",
                    )
        except Exception:                                     # noqa: BLE001
            logger.exception("[bot-pr-reaper] tick crashed")
        await asyncio.sleep(interval_s)
