"""
services/required_tool_gate.py — Part B3 (tool-call observability).

Confirmed incident: a chat message containing an EXACT file path and an
EXACT Vanguard rule-id ("Fix this critical issue: exec_usage (in
.agent/skills/plugins/.../injection_scanner.py)") got a clarifying
question ("what should change?") instead of the file being read — the
model had every fact it needed and asked anyway. Per AGENTS.md's
Architecture Rule (deterministic beats LLM judgment for exactly this
class of thing, same principle as pending_action.py's confirm-boundary),
this is a PRE-LLM, regex-based gate: if a message names a real Vanguard
rule-id AND a plausible repo file path, `read_repo_file` fires here —
before the model ever gets a turn to decide whether to ask a question —
and the real file content is injected into context, so "what should
change?" can never be the first move when the exact file was already
named.

Deliberately narrow (DO-NOT: turn this into a general NLP intent
classifier). Only fires on the EXACT rule-id vocabulary Vanguard itself
reports findings under (services/vanguard_scanner.py) — the same
vocabulary a user would be copy-pasting from ORA's own scan output —
plus a path-shaped token in the same message. Generic phrases like
"there's a security issue somewhere" never match; that's intentional.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

from services.vanguard_scanner import DANGEROUS_PATTERNS, SECRET_PATTERNS

RULE_IDS = tuple(name for name, _pat, _sev in DANGEROUS_PATTERNS) + \
           tuple(name for name, _pat, _sev in SECRET_PATTERNS)
_RULE_ID_RE = re.compile(
    r"\b(" + "|".join(re.escape(r) for r in RULE_IDS) + r")\b", re.IGNORECASE,
)
_PATH_RE = re.compile(
    r"\.?[\w][\w./\-]*\.(?:py|jsx?|tsx?|html?|css|md|jsonc?|ya?ml|go|rb|php|java|rs)\b",
)
MAX_INJECTED_CONTENT_CHARS = 8000


def detect_required_read(message: str) -> Optional[dict]:
    """Returns {"path": str, "rule_id": str} when the message names
    BOTH a known Vanguard rule-id AND a path-shaped token — else None."""
    text = message or ""
    rule_m = _RULE_ID_RE.search(text)
    if not rule_m:
        return None
    path_m = _PATH_RE.search(text)
    if not path_m:
        return None
    return {
        "path": path_m.group(0).strip("`\"'()[]{}:,"),
        "rule_id": rule_m.group(1),
    }


async def build_forced_read_context(*, message: str, ctx: dict) -> str:
    """Backward-compatible text-only wrapper — see `run_required_read_gate`
    for the structured version that also enables post-LLM enforcement."""
    gate = await run_required_read_gate(message=message, ctx=ctx)
    return gate["context"] if gate else ""


async def run_required_read_gate(*, message: str, ctx: dict) -> Optional[dict]:
    """If `detect_required_read` matches, calls the REAL read_repo_file
    tool and returns a dict with:
      "context"           — system-prompt injection text (real file
                             content, or a real "read failed" block)
      "hit"                — {"path", "rule_id"} — used by the caller
                             to check whether the LLM actually honored
                             the injection (Part B3 enforcement)
      "fallback_response"  — a fully deterministic, guaranteed-correct
                             user-facing message built directly from
                             the SAME real read result — used ONLY if
                             the LLM ignores the injected context.
      "tool_call_record"   — orchestrator-shaped {"tool","args","ok",
                             "error","elapsed_ms"} record of the read
                             this function just did — callers append
                             it to `result["tool_calls"]` so B1's
                             persisted trace (task_chain_logging.py)
                             and B2's citation_guard both see this
                             read too, not only orchestrator-dispatched
                             calls (2026-02 round-4 — this gate's read
                             happens OUTSIDE the orchestrator's own
                             tool-dispatch loop, so without this it
                             was invisible to both).
    Returns None when the gate doesn't apply to this message. Never
    raises — a gate failure must not break the turn it's meant to
    protect."""
    hit = detect_required_read(message)
    if not hit:
        return None
    _t0 = time.monotonic()
    try:
        from services.local_tools import read_repo_file
        res = await read_repo_file(ctx, {"path": hit["path"]})
    except Exception as e:                                    # noqa: BLE001
        logger.warning("required_tool_gate: read_repo_file raised: %r", e)
        res = {"ok": False, "error": str(e)}
    _elapsed_ms = round((time.monotonic() - _t0) * 1000, 1)
    _tool_call_record = {
        "tool": "read_repo_file",
        "args": {"path": hit["path"]},
        "ok": bool(res and res.get("ok")),
        "error": (res or {}).get("error"),
        "elapsed_ms": _elapsed_ms,
    }

    if not res or not res.get("ok"):
        error = (res or {}).get("error", "unknown error")
        return {
            "hit": hit,
            "tool_call_record": _tool_call_record,
            "context": (
                f"=== REQUIRED READ (rule `{hit['rule_id']}`, path `{hit['path']}`) ===\n"
                f"CRITICAL INSTRUCTION — the user named this exact file and "
                f"rule-id, so it was ALREADY read before this response was "
                f"generated. The read FAILED: {error}. Your response MUST "
                f"state plainly that the read failed and why, citing rule "
                f"`{hit['rule_id']}` and path `{hit['path']}` in your first "
                f"sentence. Do NOT ask what should change or ask the user "
                f"to restate the path/issue — they already gave both, and "
                f"asking again is the exact failure this instruction exists "
                f"to prevent.\n"
                "=== END REQUIRED READ ===\n"
            ),
            "fallback_response": (
                f"I looked for `{hit['path']}` to check the `{hit['rule_id']}` "
                f"finding you flagged, but couldn't read it: {error}. "
                f"{'Connect a project first, then ask again and I will read it and tell you if it is a real issue.' if 'project' in error.lower() else 'Once that is resolved, ask again and I will read it and tell you if it is a real issue.'}"
            ),
        }

    content = (res.get("content") or "")[:MAX_INJECTED_CONTENT_CHARS]
    return {
        "hit": hit,
        "tool_call_record": _tool_call_record,
        "context": (
            f"=== REQUIRED READ (rule `{hit['rule_id']}`, path `{hit['path']}`) ===\n"
            f"CRITICAL INSTRUCTION — the user named this exact file and "
            f"rule-id. It has ALREADY been read for you, verbatim, below. "
            f"Your response MUST cite rule `{hit['rule_id']}` and path "
            f"`{hit['path']}` and determine true-positive vs. false-positive "
            f"from this ACTUAL content. Do NOT ask \"what should change\" or "
            f"ask them to restate the path/issue — that is the exact "
            f"failure this instruction exists to prevent. If this path is "
            f"under a vendored/third-party directory (e.g. "
            f".agent/skills/plugins/), say so before proposing any in-place "
            f"patch.\n\n{content}\n"
            "=== END REQUIRED READ ===\n"
        ),
        "fallback_response": (
            f"On `{hit['path']}` (flagged for `{hit['rule_id']}`) — I read "
            f"the file; here's the relevant content so we can figure out "
            f"together whether this is a real issue:\n\n```\n{content[:1500]}\n```"
        ),
    }


def enforce_required_read_gate(gate: Optional[dict], response_text: str) -> str:
    """Part B3 enforcement (deterministic, no second LLM call) — the
    injected context in `run_required_read_gate` is advisory; models
    don't reliably obey it (confirmed live: clarifying-question and
    mangled-path failure modes both reproduced on some runs even with
    the context injected). If the gate fired this turn AND the final
    response text doesn't contain the REAL path verbatim, replace it
    outright with the deterministic `fallback_response` built straight
    from the real read result. Same "deterministic beats LLM judgment"
    principle as pending_action.py's confirm-boundary — this is not a
    suggestion, it's a guarantee. Returns `response_text` unchanged
    when the gate didn't fire, or when the response already honored
    it."""
    if not gate:
        return response_text
    text = response_text or ""
    hit = gate["hit"]
    # 2026-02 round-4 fix — confirmed live: business_voice_filter's
    # "sounds too technical" rewrite mangles the actual path token
    # (e.g. ".agent/skills/.../injection_scanner.py" -> ".that page")
    # while leaving the short, common rule-id word ("exec_usage")
    # untouched. The old `rule_id OR path` check treated that mangled
    # output as "mentions evidence" and let it ship — the exact
    # failure this gate exists to prevent, just with the path instead
    # of the whole sentence corrupted. The path is the harder, more
    # specific token to fabricate/preserve by accident, so it is now
    # REQUIRED — rule-id alone is not enough proof the real path
    # survived downstream filters.
    path_intact = hit["path"].lower() in text.lower()
    if not path_intact:
        logger.warning(
            "required_tool_gate: LLM ignored forced_read_context for rule=%s "
            "path=%s — overriding with deterministic fallback_response",
            hit["rule_id"], hit["path"],
        )
        return gate["fallback_response"]
    return text
