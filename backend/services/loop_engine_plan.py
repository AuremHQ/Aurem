"""
services/loop_engine_plan.py — P1H extraction (2026-11-25, ORA audit).

The plan-phase LLM adapter from services/loop_engine.py, moved out
verbatim (zero logic changes): `_generate_plan()` plus the two names
it is the sole owner/user of (`_CostCapPaused`, `PLAN_LLM_TIMEOUT_S`).

Why this cut (full analysis in p1h/FIX-REPORT.md):
  * a single top-level function with exactly ONE call site
    (`LoopEngine._do_plan`);
  * no `self`, no `_LIVE` registry, no Mongo writes of its own;
  * touches neither the `_parl.heal` call site (findings lane), the
    mode threading, nor the snapshot/push paths (part13 lane);
  * every heavy dependency is a lazy import inside the function, so
    importing this module stays cheap.

`services/loop_engine.py` re-exports all three names, so existing
call sites (`_do_plan`, `start()`'s `_CostCapPaused` handler,
`_do_execute`'s raise) keep resolving them unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# Iter 349 — Hard timeout on the plan-phase LLM call itself. The 120s
# phase budget covers graph refresh + repo map + LLM, but a hung LLM
# call was eating the whole budget and the user stared at
# "Generating plan…" for 90+ s (PROD P0). 30s is generous for a
# plan-only JSON response; on breach we fail FAST with a clear message
# instead of silently burning the full phase budget.
PLAN_LLM_TIMEOUT_S = int(os.environ.get("PLAN_LLM_TIMEOUT_S", "30"))


class _CostCapPaused(Exception):
    """2026-08 hardening (F2) — raised when the G13 LLM cost cap blocks
    a call mid-loop. Caught in start() and _do_execute() to PAUSE (not
    fail) the loop — "blocked ≠ failed" (C4) applied to budget. Additive
    only; does not change any other exception's handling."""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


async def _generate_plan(user_id: str, project_id: Optional[str],
                         user_message: str) -> dict:
    """Call the existing LLM to produce a structured plan.  Returns a
    dict with: title, files_to_change (list of paths), bullets (list
    of strings), estimated_time.

    Iter 212m-116 — Inject a COMPACT repo map (file paths + symbols +
    imports per file, no raw content) into the planner system prompt
    when a graph exists. Reduces planner token cost ~60% on
    repo-aware projects without losing the signal needed to pick the
    right files.

    Iter 322 — Latency profiling. Live incident loop_678eea28436c4e
    took 21.6s to plan a one-line README edit. Record per-segment
    timings (graph refresh vs repo map read vs LLM call) so the
    speed-diagnostic dashboard can identify where the wall-clock
    actually goes. Returned as `plan['_profile']` — callers stash
    it into loop_run_log under kind='plan_latency_profile'.
    """
    import time as _time
    from services.llm import call_llm_with_meta

    _t0 = _time.monotonic()
    _profile: dict = {
        "graph_refresh_s":  0.0,
        "repo_map_read_s":  0.0,
        "llm_call_s":       0.0,
        "json_parse_s":     0.0,
        "total_s":          0.0,
        "graph_refreshed":  False,
        "repo_map_present": False,
    }

    # Inject the compact repo map if available (cheap — single DB read).
    # Iter 212m-117 — Auto-refresh stale graphs (>30 min old) before
    # building the map so the planner always sees the current state of
    # the user's repo. Incremental build (iter 113) means an unchanged
    # repo costs ZERO new LLM tokens to refresh — just a regex pass +
    # blob_sha diff. Best-effort: a refresh failure must NOT block plan.
    repo_map_block = ""
    try:
        from cto_services.db import get_db
        from services.repo_map import build_repo_map
        db = get_db()
        # Stale-check the graph; if >30 min old, rebuild silently.
        if db is not None and project_id:
            try:
                from services.graph_builder import build_graph
                import time as _time
                prior = await db.project_graphs.find_one(
                    {"project_id": project_id, "user_id": user_id},
                    {"_id": 0, "built_at": 1},
                )
                age = _time.time() - (
                    (prior or {}).get("built_at") or 0
                ) if prior else float("inf")
                if (not prior) or age > 30 * 60:
                    proj = await db.cto_projects.find_one(
                        {"project_id": project_id, "user_id": user_id},
                        {"_id": 0, "github_owner": 1, "github_repo": 1,
                         "github_branch": 1, "github_token": 1,
                         "auth_method": 1, "installation_id": 1, "user_id": 1},
                    )
                    if proj and proj.get("github_owner") and proj.get("github_repo"):
                        # 2026-02-11 · Phase 3b (Bug 2 fix) — App-installed projects.
                        # 2026-06 PAT-removal — App-only.
                        from services.pat_vault import get_repo_token_or_error
                        tok, _g_err, _ = await get_repo_token_or_error(proj)
                        if tok:
                            logger.info(
                                "[plan] graph stale (age=%.0fs) — rebuilding silently",
                                age if age != float("inf") else -1,
                            )
                            _t_gr0 = _time.monotonic()
                            await build_graph(
                                db=db, project_id=project_id, user_id=user_id,
                                gh_token=tok,
                                gh_owner=proj["github_owner"],
                                gh_repo=proj["github_repo"],
                                branch=proj.get("github_branch") or "main",
                            )
                            _profile["graph_refresh_s"] = round(
                                _time.monotonic() - _t_gr0, 3,
                            )
                            _profile["graph_refreshed"] = True
                        else:
                            # 2026-08-27 · Journey Watch investigation Finding 2
                            # (secondary): this branch used to be a true silent
                            # no-op — no log, no signal, indistinguishable from
                            # "graph was already fresh". This is the confirmed
                            # gate that (combined with the admin display bug,
                            # fixed separately) explained the 0%-graph figure.
                            logger.warning(
                                "[plan] graph build skipped for project=%s user=%s: "
                                "no usable GitHub token (%s)",
                                project_id, user_id, _g_err or "unknown",
                            )
                    elif proj is not None:
                        logger.warning(
                            "[plan] graph build skipped for project=%s user=%s: "
                            "project missing github_owner/github_repo",
                            project_id, user_id,
                        )
            except Exception as e:                          # noqa: BLE001
                logger.debug("[plan] silent graph refresh skipped: %r", e)
        _t_rm0 = _time.monotonic()
        rm = await build_repo_map(db, project_id, user_id)
        _profile["repo_map_read_s"] = round(
            _time.monotonic() - _t_rm0, 3,
        )
        if rm.get("has_map"):
            repo_map_block = (
                "\n\n--- COMPACT REPO MAP ({n} files, {c} chars) ---\n"
                "Each line: <path> [<layer>] · symbols: ... · imports: ... · // <desc>\n"
                "Use this to pick exact files to change. Do NOT invent paths.\n\n"
                "{m}\n"
                "--- END REPO MAP ---\n"
            ).format(n=rm["file_count"], c=rm["char_count"], m=rm["map_text"])
            _profile["repo_map_present"] = True
            logger.info(
                "[plan] injected repo map: %d files, %d chars",
                rm["file_count"], rm["char_count"],
            )
    except Exception as e:                              # noqa: BLE001
        logger.debug("[plan] repo map injection skipped: %r", e)

    from services.identity import OR_IDENTITY
    sys_msg = (
        # 2026-08 · Naming & Identity Canon PR — identity line now
        # sourced from services/identity.py::OR_IDENTITY (the SAME
        # constant orchestrator.py's chat persona uses) instead of a
        # hand-written "You are ORA, an AI CTO" opener.
        OR_IDENTITY + "  The user is in Loop Mode and needs a "
        "PLAN ONLY (no code).  Respond with strict JSON:\n"
        '{\n'
        '  "title": str (3-7 words),\n'
        '  "files_to_change": [list of paths you will touch],\n'
        '  "bullets":         [3-7 short numbered steps],\n'
        '  "estimated_time":  str (e.g. "~3 minutes")\n'
        '}\n'
        "Return ONLY the JSON object — no markdown, no commentary."
        + repo_map_block
    )
    # Iter 349 — hard 30s cap on the LLM call itself so a hung provider
    # fails fast with a clear message instead of eating the 120s phase
    # budget while the user watches "Generating plan…" (PROD P0).
    try:
        meta = await asyncio.wait_for(
            call_llm_with_meta(sys_msg, user_message,
                               review_mode="pro",
                               mode="loop_plan",
                               feature="Loop Planning"),
            timeout=PLAN_LLM_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"Plan LLM call timed out after {PLAN_LLM_TIMEOUT_S}s — the "
            "model didn't respond in time. Try again, or rephrase with a "
            "shorter request."
        )
    # 2026-08 hardening (F2) — additive: cost-cap breach comes back as a
    # normal ok:False dict (see _meta.py), not an exception. Raise the
    # distinguishable _CostCapPaused so start()'s except clause PAUSES
    # (not fails) the loop.
    if isinstance(meta, dict) and meta.get("error_code") == "COST_CAP_REACHED":
        raise _CostCapPaused(meta.get("error") or "Monthly task budget reached.")
    _profile["llm_call_s"] = round(
        _time.monotonic() - _t0 - _profile["graph_refresh_s"]
        - _profile["repo_map_read_s"], 3,
    )
    content = (meta or {}).get("content", "").strip()
    # Tolerate a stray ```json fence.
    if content.startswith("```"):
        first_nl = content.find("\n")
        content = content[first_nl + 1:]
        if content.endswith("```"):
            content = content[:-3].rstrip()
    _t_jp0 = _time.monotonic()
    try:
        plan = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        plan = {
            "title": "Plan",
            "files_to_change": [],
            "bullets": [content[:500] or "Unable to parse plan."],
            "estimated_time": "?",
            "raw": content[:2000],
        }
    _profile["json_parse_s"] = round(
        _time.monotonic() - _t_jp0, 3,
    )
    _profile["total_s"] = round(_time.monotonic() - _t0, 3)
    # Attach the profile so the caller (`_do_plan`) can persist it.
    if isinstance(plan, dict):
        plan["_profile"] = _profile
    logger.info(
        "[plan] LATENCY PROFILE — total=%.2fs · graph=%.2fs · "
        "repo_map=%.2fs · llm=%.2fs · json=%.2fs · "
        "refreshed=%s · map_present=%s",
        _profile["total_s"], _profile["graph_refresh_s"],
        _profile["repo_map_read_s"], _profile["llm_call_s"],
        _profile["json_parse_s"], _profile["graph_refreshed"],
        _profile["repo_map_present"],
    )
    return plan
