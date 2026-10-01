"""
services/session_state.py — answers that come from stored records, not
from a model.

Greetings, a bare "?" and "any issues in this project" used to go to a
tool-free LLM that could say anything ("Clean bill of health on the
scans", "that checklist PR is ready") with no scan or PR behind it. These
turns are now answered from the database: what is recorded for this
project, and what is waiting for the user. If nothing is recorded the
reply says so and offers the next step; it never claims a scan result.
"""
from __future__ import annotations

import re

_GREETINGS = {"hi", "hii", "hello", "hey", "yo", "hola", "namaste", "gm", "morning",
              "good morning", "good evening", "good afternoon"}
_STATUS = {"?", "??", "???", "what now", "what next", "whats next", "status",
           "next ?", "next?", "anything?", "any update", "any updates"}
_ISSUE_WORDS = re.compile(
    r"\b(?:issues?|problems?|bugs?|vulnerabilit\w+|findings?|errors?|risks?)\b", re.I)
# Asking about GitHub's own Issues tracker / PRs is a different request.
_GITHUB_ISSUES = re.compile(r"\bgit\s?hub\b.*\bissues?\b|\bissues?\s*#?\d+|\bpull requests?\b|\bprs?\b", re.I)
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _norm(prompt: str) -> str:
    return re.sub(r"\s+", " ", (prompt or "").strip().lower()).rstrip("!.")


def route_kind(prompt: str, project_id: str | None) -> str | None:
    """greeting | status | issues | None. Conservative: anything not
    clearly one of these goes to the normal pipeline."""
    text = _norm(prompt)
    if text in _GREETINGS:
        return "greeting"
    if text in _STATUS:
        return "status"
    has_project = bool(project_id) and project_id != "home"
    if has_project and len(text.split()) <= 12 and _ISSUE_WORDS.search(text) \
            and not _GITHUB_ISSUES.search(text):
        return "issues"
    return None


async def load_state(db, *, user_id: str, project_id: str | None, session_id: str) -> dict:
    """What the database actually holds. Any DB problem yields an empty
    state, which the renderers describe honestly as "nothing on record"."""
    state = {"recorded": 0, "open": [], "waiting_changes": 0}
    try:
        if project_id and project_id != "home":
            base = {"user_id": user_id, "project_id": project_id}
            state["recorded"] = await db.cto_open_findings.count_documents(base)
            rows = await db.cto_open_findings.find(
                {**base, "status": "open"},
                {"_id": 0, "severity": 1, "title": 1, "file": 1}).to_list(200)
            state["open"] = sorted(rows, key=lambda r: _SEV_ORDER.get(r.get("severity"), 9))
        if session_id:
            state["waiting_changes"] = await db.pending_actions.count_documents(
                {"session_id": session_id, "user_id": user_id, "status": "AWAITING_CONFIRM"})
    except Exception:  # noqa: BLE001
        return {"recorded": 0, "open": [], "waiting_changes": 0}
    return state


def _findings_line(state: dict) -> str:
    n = len(state["open"])
    if n:
        return f"{n} open finding{'s' if n != 1 else ''} recorded for this project."
    if state["recorded"]:
        return "No open findings are recorded for this project."
    return "No scan results are recorded for this project yet."


def _waiting_line(state: dict) -> str:
    n = state["waiting_changes"]
    return f"{n} change{'s' if n != 1 else ''} waiting for your approval." if n else ""


def render(kind: str, state: dict, has_project: bool) -> str:
    if kind == "issues":
        return render_issues(state)
    lines = [_findings_line(state)] if has_project else []
    if _waiting_line(state):
        lines.append(_waiting_line(state))
    if kind == "greeting":
        head = "Hi."
        tail = "What would you like to work on?"
    else:
        head = "Here is what I have on record."
        tail = ("Say what you want next, or ask me to run a scan."
                if has_project else "Say what you want to work on.")
    return " ".join([head, *lines, tail])


def render_issues(state: dict) -> str:
    if not state["open"]:
        tail = ("Want me to run a scan now?" if not state["recorded"]
                else "Want me to run a fresh scan?")
        return f"{_findings_line(state)} {tail}"
    shown = state["open"][:5]
    items = "\n".join(
        f"- [{r.get('severity', '?')}] {r.get('title', 'untitled')}"
        + (f" ({r['file']})" if r.get("file") else "") for r in shown)
    more = len(state["open"]) - len(shown)
    tail = f"\n...and {more} more." if more > 0 else ""
    return f"{_findings_line(state)} Most serious first:\n{items}{tail}"
