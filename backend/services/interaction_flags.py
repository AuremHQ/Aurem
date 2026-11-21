"""services/interaction_flags.py — leaf helpers for the opt-in interaction boundary.

Pure, dependency-free helpers (stdlib only): the INTERACTION_CONTRACT_V1
flag check, the ChatPanel attachment-markdown stripper, and the
factual/read-only prompt classifiers. They used to live in
services/interaction_contract.py (flag + classifiers) and
services/mode_b_council.py (attachment stripper), which made
mode_b_council / local_tools / task_chain_logging / tool_failure_evidence
all import interaction_contract — a 7-node circular-import SCC that
capped the Architecture health score (circ x15 each). Nothing here
imports any other service, so the cycle is broken at the root; the
previous homes re-export these names, so every existing importer keeps
working unchanged.

Moved 2026-11-20 (health-score architecture fix) — verbatim moves,
zero logic changes.
"""
from __future__ import annotations

import os
import re

_NO_WRITE = re.compile(
    r"\b(?:do not|don't|don\u2019t|never)\s+(?:touch|change|modify|edit|write|update|delete)"
    r"\s+(?:anything|(?:(?:any|the)\s+)?(?:files?|code)|this repo)\b", re.I)


def enabled() -> bool:
    return os.environ.get("INTERACTION_CONTRACT_V1") == "1"


# Iter 110 — recognise the ChatPanel.jsx attachment markdown shape so we
# only run council-detection on the user's ACTUAL typed text, not on
# pasted file/image content that happens to contain trigger words.
# ChatPanel emits attachments as separate blocks joined by "\n\n":
#   [\U0001f5bc\ufe0f file.png \u00b7 100 KB] / [\U0001f4c4 doc.pdf \u00b7 50 KB] / [File: foo.txt]
# followed by the markdown body, then the user-typed text last.
_ATTACHMENT_HEADER_RE = re.compile(
    r"^\s*\[(?:\U0001f5bc\ufe0f|\U0001f4c4|File:)",
    re.IGNORECASE,
)


def _strip_attachment_blocks(message: str) -> str:
    """Remove attachment-markdown blocks emitted by ChatPanel.jsx so
    only the user-typed text remains.

    ChatPanel.jsx always builds the prompt as:
        [attachment header]
        <OCR/file body>

        [attachment header]
        <OCR/file body>

        <user-typed text>      \u2190 always LAST

    Strategy: drop the leading "[Working on project: ...]" auto-context.
    If any line then matches an attachment header, return ONLY the final
    "\n\n"-separated block (the user-typed text). Otherwise return
    the whole message unchanged."""
    if not message:
        return ""
    text = re.sub(
        r"^\[Working on project: [^\]]+\]\s*\n*",
        "",
        message,
        flags=re.MULTILINE,
    )
    has_attachment = any(
        _ATTACHMENT_HEADER_RE.match(ln) for ln in text.splitlines()
    )
    if not has_attachment:
        return text.strip()
    blocks = re.split(r"\n\s*\n", text)
    # Walk backwards and take the first block that's NOT an attachment
    # header — that's the user's typed text.
    for block in reversed(blocks):
        if block.strip() and not _ATTACHMENT_HEADER_RE.match(block):
            return block.strip()
    return ""


def _instructions(prompt: str) -> str:
    # Quoted examples, source code and attached evidence are not instructions.
    text = _strip_attachment_blocks(prompt or "")
    return re.sub(r'```[\s\S]*?```|"[^"\n]*"|`[^`\n]*`', '', text)


def factual_lookup(prompt: str) -> bool:
    """Bounded factual/status boundary, not a general intent classifier."""
    text = _instructions(prompt)
    parts = [re.sub(r'^\s*\d+[.)]\s*', '', s).strip()
             for s in re.split(r'[\n?]+', text) if s.strip()]
    return bool(parts) and all(re.match(
        r"^(?:what\s+(?:is|are|was|were)|which\s+.+\s+(?:is|are)|"
        r"how many|where\s+(?:is|are)|has\s+.+|have\s+.+|"
        r"(?:council|audit|error)\s+(?:queue|status|count)|"
        r"(?:show|list|report)\s+(?:the\s+)?(?:current\s+)?(?:status|counts))\b",
        s, re.I) for s in parts)


def read_only_request(prompt: str) -> bool:
    return factual_lookup(prompt) or bool(_NO_WRITE.search(_instructions(prompt)))
