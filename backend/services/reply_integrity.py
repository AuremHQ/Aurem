"""
services/reply_integrity.py — structural checks on a reply's code blocks,
and the file names the user already used in this conversation.

Why: the business-voice filter used to treat an unclosed ``` fence as
plain prose (rewriting the paths and language tag inside it), and a
model that emitted a code block of nothing but empty numbered lines was
delivered as if it were a draft. Both are fixed by looking at the
block's structure, not at specific phrases.
"""
from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```([^\n`]*)\n([\s\S]*?)(?:```|\Z)")
_EMPTY_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*$")
EMPTY_BLOCK_NOTE = "_(That section came out empty, so there is nothing to show yet.)_"


def _is_empty_body(body: str) -> bool:
    return all(_EMPTY_LINE_RE.match(line) for line in body.splitlines())


def repair_code_blocks(text: str) -> str:
    """Drop code blocks that contain no content (only blank or bare list
    markers) and close a fence the model left open. Content is never
    edited; a block with anything real in it is returned byte-for-byte."""
    if not text or "```" not in text or "aurem-handoff" in text:
        return text

    def _fix(m: re.Match) -> str:
        if _is_empty_body(m.group(2)):
            return EMPTY_BLOCK_NOTE
        return m.group(0) if m.group(0).endswith("```") else m.group(0).rstrip("\n") + "\n```"

    return _FENCE_RE.sub(_fix, text)


async def user_named_files(session_id: str, user_id: str, user_prompt: str = "") -> set[str]:
    """File paths the user themselves wrote, this turn or earlier in the
    session: ORA may call them by their real name. Fail-open (empty set)
    on any DB problem, which only means the old behaviour applies."""
    from services.output_guard import extract_named_files
    names = set(extract_named_files(user_prompt or ""))
    if not session_id:
        return names
    try:
        from cto_services.db import get_db
        doc = await get_db().chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id}, {"_id": 0, "turns": 1})
        for turn in (doc or {}).get("turns") or []:
            if isinstance(turn, dict) and turn.get("role") == "user":
                names |= extract_named_files(turn.get("content") or "")
    except Exception:  # noqa: BLE001
        return names
    return names


_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]*\s*")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def strip_prompt_echo(reply: str, system_prompt: str, user_prompt: str = "") -> str:
    """Remove sentences the model copied word for word out of the
    instructions it was given (e.g. "Short and simple!"). Structural: the
    test is whether the whole sentence occurs inside the prompt we sent,
    so it needs no list of phrases and also covers admin-edited rules we
    never see in the repo. Code blocks and anything the user said
    themselves are left alone."""
    if not reply or not system_prompt or "```" in reply:
        return reply
    haystack = f" {_norm(system_prompt)} "
    user_norm = f" {_norm(user_prompt)} "
    kept = []
    for sentence in _SENTENCE_RE.findall(reply):
        n = _norm(sentence)
        if len(n.split()) >= 3 and f" {n} " in haystack and f" {n} " not in user_norm:
            continue
        kept.append(sentence)
    out = "".join(kept).strip()
    return out or reply
