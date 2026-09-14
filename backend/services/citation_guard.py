"""
citation_guard.py — Core 1 of the verification foundation.

Hard-blocks LLM responses that reference file paths, version numbers,
or dependency counts WITHOUT a corresponding `read_repo_file` /
`read_repo_files` tool call in the same turn. On detection it
auto-fetches the cited paths and re-runs the LLM once with the real
content injected as system context — so the user never sees a
hallucinated reference.

Wires into the orchestrator's response post-process step:

    draft = await llm.respond(messages)
    final = await CitationGuard().enforce(
        response_text=draft,
        tool_calls=this_turn_tool_calls,
        ctx=ctx,
        llm_caller=llm.respond,
        original_messages=messages,
    )
    return final  # ← only this ever reaches the frontend

Iter 209 — Aurem core architecture.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

# ─── Patterns that demand a source ───────────────────────────────────
# Matches anything that *looks like* a verifiable fact: file paths,
# semver-like numbers, package/dependency counts. Conservative on
# purpose — false negatives are better than false positives because
# false positives trigger an unnecessary refetch + retry.
_FILE_PATH = re.compile(
    r"""
    (?<![\w/])                          # not mid-word
    (?:[\w\-.]+/){1,}                   # at least one slash segment
    [\w\-.]+
    \.(?:py|jsx|js|ts|tsx|md|json|txt|yaml|yml|toml|env|ini|sh|css|html|sql)
    \b
    """,
    re.VERBOSE,
)
_VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
_COUNT = re.compile(
    r"\b\d{2,4}\+?\s*(?:packages?|dependencies|modules|files|lines)\b",
    re.IGNORECASE,
)

# ─── Part B2 — "verified via X" tool-name claims ─────────────────────
# Confirmed incident class: a response states a specific TOOL was used
# to check/verify something ("verified via frontend_audit", "confirmed
# by the read_repo_file tool") when no such tool call exists in this
# turn's real trace. This is a distinct claim shape from a bare file
# path — captured separately so it can be checked against the actual
# `tool_calls` list (Part B1's `get_turn_tool_records`) rather than
# against `read_repo_file` paths specifically.
_TOOL_CLAIM = re.compile(
    r"\b(?:verified|checked|confirmed|validated)\s+(?:via|using|with|by)\s+"
    r"(?:the\s+)?`?([a-z][a-z0-9_]{2,40})`?\s*(?:tool|call)?\b",
    re.IGNORECASE,
)


def _extract_tool_claims(text: str) -> list[str]:
    """Distinct tool names the response claims were used to verify
    something. Deliberately narrow — only the explicit "verified via
    X" phrasing shape, not vague prose like "I double-checked this"."""
    if not text:
        return []
    return sorted({m.group(1).lower() for m in _TOOL_CLAIM.finditer(text)})


def _extract_claims(text: str) -> dict[str, list[str]]:
    """Return distinct file paths / versions / counts mentioned in `text`."""
    if not text:
        return {"paths": [], "versions": [], "counts": []}
    paths    = sorted(set(_FILE_PATH.findall(text)))
    versions = sorted(set(_VERSION.findall(text)))
    counts   = sorted(set(_COUNT.findall(text)))
    return {"paths": paths, "versions": versions, "counts": counts}


def _read_paths_this_turn(tool_calls: Iterable[dict]) -> set[str]:
    """Set of repo paths that were actually read via `read_repo_file*`
    in THIS turn. `tool_calls` is the orchestrator's per-turn record:

        [{"tool": "read_repo_file",  "args": {"path": "x.py"}, ...},
         {"tool": "read_repo_files", "args": {"paths": ["a", "b"]}, ...}]
    """
    out: set[str] = set()
    for tc in tool_calls or ():
        name = tc.get("tool") or tc.get("name") or ""
        args = tc.get("args") or tc.get("arguments") or {}
        if name == "read_repo_file":
            p = (args.get("path") or "").strip()
            if p:
                out.add(p)
        elif name == "read_repo_files":
            for p in (args.get("paths") or []):
                if isinstance(p, str) and p.strip():
                    out.add(p.strip())
    return out


class CitationGuard:
    """Stateless guard — instantiate once, reuse across turns."""

    @staticmethod
    def _flag_unverified_tool_claim(ctx: dict, claimed_tools: list[str]) -> None:
        """Part B2 — records the block so the router layer (which has
        a real `db` handle, unlike this deep in the call stack) can
        persist a `unverified_tool_claim_blocked` event. Mirrors the
        existing `ctx.setdefault("system_signals", [])` pattern in
        `local_tools.py::invoke_local_tool` — accumulate in-memory
        during the turn, harvest once at the end."""
        try:
            ctx.setdefault("unverified_tool_claims_blocked", []).append({
                "claimed_tools": claimed_tools,
                "ts": time.time(),
            })
        except Exception as e:                                # noqa: BLE001
            logger.warning("CitationGuard: failed to flag unverified tool claim: %r", e)

    def verify(
        self,
        response_text: str,
        tool_calls: Iterable[dict],
    ) -> dict[str, Any]:
        """Return a structured report. Does NOT mutate the response.

        Shape::

            {
              "pass":          bool,
              "unverified_paths": [str, ...],
              "unverified_versions": [str, ...],
              "unverified_counts": [str, ...],
              "claims": {paths|versions|counts},
              "read_paths": {str, ...},
            }

        `versions` and `counts` cannot be verified by file path alone,
        so we only block on `paths`. Versions/counts are surfaced as a
        weak signal in the audit log.
        """
        claims = _extract_claims(response_text)
        read   = _read_paths_this_turn(tool_calls)
        unverified_paths = [p for p in claims["paths"] if p not in read]

        # Part B2 — "verified via X" tool-name claims, checked against
        # the REAL set of tools this turn actually called (Part B1's
        # get_turn_tool_records). Only known local tool names count —
        # this stays narrow so ordinary prose never false-triggers.
        from services.local_tools import LOCAL_TOOLS
        from services.task_chain_logging import get_turn_tool_records
        claimed_tools = [
            t for t in _extract_tool_claims(response_text) if t in LOCAL_TOOLS
        ]
        called_tools = get_turn_tool_records(list(tool_calls or []))
        unverified_tool_claims = [t for t in claimed_tools if t not in called_tools]

        return {
            "pass":                  len(unverified_paths) == 0 and len(unverified_tool_claims) == 0,
            "unverified_paths":      unverified_paths,
            "unverified_versions":   claims["versions"] if not read else [],
            "unverified_counts":     claims["counts"]   if not read else [],
            "unverified_tool_claims": unverified_tool_claims,
            "claims":                claims,
            "read_paths":            sorted(read),
        }

    async def enforce(
        self,
        response_text: str,
        tool_calls: Iterable[dict],
        ctx: dict,
        llm_caller: Callable[..., Awaitable[str]],
        original_messages: list[dict] | None = None,
        read_repo_file: Callable[..., Awaitable[dict]] | None = None,
        max_fetch: int = 6,
    ) -> dict[str, Any]:
        """Verify the response and, if it has unsourced file paths,
        auto-fetch them and re-run the LLM with verified content.

        Returns::

            {"text": str, "guard": <verify report>, "retried": bool,
             "fetched": {path: content_or_error}}

        `read_repo_file` is injected so tests can mock without standing
        up the real GitHub layer.
        """
        report = self.verify(response_text, tool_calls)
        if report["pass"]:
            return {"text": response_text, "guard": report, "retried": False, "fetched": {}}

        # Lazy-import to avoid circular deps when this module is loaded
        # by light-weight scripts (tests, scripts/, etc.).
        if read_repo_file is None:
            from services.local_tools import read_repo_file as _rrf
            read_repo_file = _rrf

        fetched: dict[str, str] = {}
        for path in report["unverified_paths"][:max_fetch]:
            try:
                r = await read_repo_file(ctx, {"path": path})
                if r and r.get("ok") and "content" in r:
                    fetched[path] = (r["content"] or "")[:8000]
                else:
                    fetched[path] = f"FILE NOT FOUND: {path}"
            except Exception as e:                 # noqa: BLE001
                logger.warning("CitationGuard fetch %s failed: %r", path, e)
                fetched[path] = f"FILE NOT FOUND: {path}"

        # Build the injection block. Truncate per file to keep the
        # retry prompt under a sane size.
        injection_parts = [
            f"### VERIFIED FILE — {p}\n```\n{c}\n```"
            for p, c in fetched.items()
        ]
        tool_claims = report.get("unverified_tool_claims") or []
        header_lines = []
        if fetched:
            header_lines.append(
                "Your previous draft referenced files you did not read. "
                "REWRITE your answer using ONLY the content below. "
                "For any path marked `FILE NOT FOUND`, say so explicitly "
                "instead of guessing."
            )
        if tool_claims:
            # Part B2 — the response claimed to have used a tool (e.g.
            # "verified via X") that the real trace shows was NEVER
            # called this turn. There is no file to auto-fetch here —
            # this is a false process claim, not an unsourced fact —
            # so the remediation is: strip the claim and say honestly
            # it wasn't actually verified, exactly like a hedged claim
            # would have read in the first place.
            header_lines.append(
                "Your previous draft claimed to have used the "
                f"following tool(s) to verify something: {', '.join(tool_claims)}. "
                "The real tool-call trace for this turn shows NONE of "
                "these were actually called. REMOVE that claim entirely — "
                "say plainly that this was not actually verified this turn, "
                "instead of stating it as fact."
            )
            self._flag_unverified_tool_claim(ctx, tool_claims)
        injection = (
            "── CITATION GUARD: VERIFIED CONTENT / PROCESS CHECK ──\n"
            + " ".join(header_lines)
            + " This rewrite is a plain text answer only — do NOT emit a "
            "`tool_call` fence or request any more tools here; there is "
            "no tool loop to run them.\n\n"
            + "\n\n".join(injection_parts)
        )

        # Re-run the LLM with the injection appended as a system note.
        # We append rather than replace so the user's original prompt
        # and the model's prior thought stay in scope.
        try:
            new_text = await llm_caller(
                original_messages=original_messages,
                additional_context=injection,
                instruction="Rewrite using ONLY verified file contents above.",
            )
        except TypeError:
            # Fallback signature if the caller expects (messages,) only.
            extra_msg = [{"role": "system", "content": injection}]
            new_text = await llm_caller((original_messages or []) + extra_msg)

        # 2026-08-23 — this rewrite is a single-shot completion with no
        # tool-execution loop behind it. If the model still emits a
        # `tool_call` fence (habit from the main system prompt), it
        # would otherwise leak straight into the chat bubble as raw
        # JSON/XML text — a real prod symptom. Strip it defensively.
        try:
            from services.tools_bridge import strip_tool_calls
            new_text = strip_tool_calls(new_text) if new_text else new_text
        except Exception as _se:                   # noqa: BLE001
            logger.warning("CitationGuard: strip_tool_calls skipped: %r", _se)

        return {
            "text":     new_text or response_text,
            "guard":    report,
            "retried":  True,
            "fetched":  fetched,
        }
