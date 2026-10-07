"""
services/tool_retry.py — one automatic retry for a read-only tool that
failed for a transient reason.

A single dropped GitHub call used to end the turn with "the call failed,
want me to try again?". Read-only tools are safe to repeat, so a fast
network / 5xx / rate-limit failure is retried once. Auth and not-found
errors, stalls (timeouts), and every tool that changes something are
never retried.
"""
from __future__ import annotations

import re

from services.local_tools import _SCOPE_DISCOVERY_TOOLS

RETRY_SAFE_TOOLS = frozenset(_SCOPE_DISCOVERY_TOOLS | {"list_issues", "fetch_url"})
RETRY_DELAY_S = 0.5

_NOT_TRANSIENT = re.compile(
    r"\b(?:401|403|404|410|422|unauthori[sz]ed|forbidden|not found|bad credentials|"
    r"invalid[_\s-]?token|missing[_\s-]?scope)\b", re.I)
_TRANSIENT = re.compile(
    r"\b(?:429|500|502|503|504|rate.?limit|temporar\w+|connection (?:reset|refused|error|aborted)|"
    r"network|bad gateway|service unavailable|econnreset|remote(?:ly)? disconnected)\b", re.I)


def should_retry(tool_name: str, res: dict | None) -> bool:
    if tool_name not in RETRY_SAFE_TOOLS or not isinstance(res, dict) or res.get("ok"):
        return False
    if res.get("timed_out"):
        return False
    error = str(res.get("error") or "")
    return bool(_TRANSIENT.search(error)) and not _NOT_TRANSIENT.search(error)
