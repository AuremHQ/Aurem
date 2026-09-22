"""V1 separates immutable evidence from cosmetic prose transformations.

No keyword-selected replacement task, no lossful LLM compression, no filename
humanization. Safety guards may BLOCK a reply, but may not invent another task.
"""
from __future__ import annotations

import re

_LITERALS = re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`')
_TOKEN = re.compile(r'\bsk[-_]live[-_][A-Za-z0-9]{20,}\b')
BLOCKED_REPLY = (
    "I couldn't validate this response against the current request. "
    "No completion is confirmed. Please clarify the intended outcome before I proceed."
)


def render_safe_content(content: str, guard) -> tuple[str, bool]:
    from services.task_chain_logging import redact_secrets
    # Credentials must be redacted even inside code/evidence and skip paths.
    safe = _TOKEN.sub("[REDACTED]", redact_secrets(content))
    pieces = []
    end = 0
    for match in _LITERALS.finditer(safe):
        prose = safe[end:match.start()]
        if guard(prose) != prose:
            return BLOCKED_REPLY, True
        pieces.extend((prose, match.group()))
        end = match.end()
    prose = safe[end:]
    if guard(prose) != prose:
        return BLOCKED_REPLY, True
    pieces.append(prose)
    return ''.join(pieces), False