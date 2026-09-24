"""Bounded signatures from observed outcomes, independent of reply success."""
from __future__ import annotations

import re


def turn_tool_calls(result: dict | None) -> list:
    """Loop-budget exits use tool_invocations; normal exits often use tool_calls."""
    if not isinstance(result, dict):
        return []
    from services.interaction_contract import enabled
    if enabled() and isinstance(result.get("tool_invocations"), list):
        return result["tool_invocations"]
    return result.get("tool_calls") or []


def failure_signatures(calls: list | None) -> list[str]:
    signatures = set()
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        result = call.get("result")
        result = result if isinstance(result, dict) else {}
        if call.get("ok") is not False and result.get("ok") is not False:
            continue
        # No exception message, arguments, path, tenant data or secrets in a key.
        name = re.sub(r'[^a-zA-Z0-9_]', '', str(call.get("tool") or call.get("name") or "unknown"))[:80]
        category = str(call.get("error_class") or result.get("error_class") or "tool_failed")
        category = re.sub(r'[^a-zA-Z0-9_]', '', category)[:60]
        signatures.add(f"tool:{name}:{category}")
    return sorted(signatures)