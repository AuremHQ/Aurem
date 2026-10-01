"""
tests/test_chat_reliability_replay_phase0.py

Replays the 11-turn production transcript (owner test on
AuremHQ/secure-team-workspace) through the REAL chat pipeline —
`chat_send` and `/chat/stream` — with only the LLM stubbed to the exact
texts ORA produced. Everything after the LLM (pre-LLM routing, intent
tiers, output guards, business-voice filter, interaction contract,
session state) is the real code.

Each test states the CORRECT behaviour and is `xfail(strict=True)`: it
fails today (that is the bug), and the strict flag forces whoever fixes
it to delete the marker, so a fix can't ship without this test going
green. See docs/chat_reliability/PHASE0_REPORT.md.
"""
import asyncio
import json
import os
import re

import pytest

USER = [
    "hi", "hi", "show me issues", "?", "any issue in my this project",
    "docs/OPERATIONS.md build it", "Please continue from where you left off.",
    "continue", "continue next", "next ?", "ok",
]
_EMPTY = lambda n: "\n".join(f"{i}." for i in range(1, n + 1))  # noqa: E731
ORA = [
    "Clean bill of health on the scans — nothing urgent to tackle. What's next on your mind?",
    "Hey! That checklist PR is ready for your review. What else can I help you knock out?",
    "I tried to pull your GitHub issues but the call failed on my end. Want me to try again?",
    "I'm here and ready to help with the project — just let me know what you'd like me to tackle next from the Phase 1 checklist or anything else. Short and simple!",
    "Neither of the requested documentation files exists in the verified content. docs/BLUPRINT.md was not found. docs/OPERATIONS.md was not found. I have no information about these files' contents to share.",
    "I'll create a fresh docs/OPERATIONS.md with core operational guidelines. One moment while I draft it. (ORA begins silently assembling the page structure and content before showing the full draft)",
    "Continuing — here's the draft docs/OPERATIONS.md I'm assembling:\n```markdown\n" + _EMPTY(15) + "\n```",
    "Continuing — here's the rest of the draft for docs/OPERATIONS.md:\n```markdown\n" + _EMPTY(13) + "\n```",
    "Here's the rest of the Security section draft for docs/OPERATIONS.md:\n```markdown\n4. Security\n- Rotate production credentials quarterly via `docs/OPERATIONS.md`.\n- All third-party dependencies must be pinned to exact versions in docs/OPERATIONS.md.\n",
    "Let's finish the Scaling section in docs/OPERATIONS.md and then I'll submit the full draft as a PR for your review. (Standby — building that now...)",
    "Okay.",
]


def _install(monkeypatch, cur):
    import importlib

    from tests._mini_motor import AsyncMongoMockClient

    import routers.chat as chat_mod
    import services.llm as llm
    import services.orchestrator as orch
    from cto_services.db import set_db

    set_db(AsyncMongoMockClient()["t"])

    async def _dev(*a, **k):
        return {"user_id": "u1", "email": "u@example.com", "tier": "pro", "is_founder": False}

    async def _noop(*a, **k):
        return None

    for mod in (chat_mod.turn, chat_mod.stream, chat_mod.worker):
        if hasattr(mod, "current_dev"):
            monkeypatch.setattr(mod, "current_dev", _dev)
    for t in ("services.usage.assert_has_budget", "services.usage.assert_has_task_budget"):
        m, fn = t.rsplit(".", 1)
        monkeypatch.setattr(importlib.import_module(m), fn, _noop)

    async def _llm(messages=None, system="", **kw):
        return ORA[cur[0]]

    async def _cwt(**kw):
        return {"content": ORA[cur[0]], "provider": "stub", "tool_calls": [],
                "tool_invocations": [], "tool_calls_run": 0, "messages": []}

    monkeypatch.setattr(llm, "call_llm", _llm)
    monkeypatch.setattr(orch, "chat_with_tools", _cwt)
    for mod in (chat_mod.turn, chat_mod.stream, chat_mod.worker):
        if hasattr(mod, "chat_with_tools"):
            monkeypatch.setattr(mod, "chat_with_tools", _cwt)


async def _replay_send(cur):
    from routers.chat import turn as turn_mod
    out = []
    for i, u in enumerate(USER):
        cur[0] = i
        r = await turn_mod.chat_send(
            body=turn_mod.ChatBody(prompt=u, session_id="replay-send"), authorization="Bearer x")
        out.append({"text": r.get("content") or "", "interaction": r.get("interaction") or {}})
    return out


def _replay_stream(cur):
    from fastapi.testclient import TestClient

    from main import app
    c = TestClient(app)
    out = []
    for i, u in enumerate(USER):
        cur[0] = i
        r = c.post("/api/aurem-dev/chat/stream",
                   json={"prompt": u, "session_id": "replay-stream"},
                   headers={"Authorization": "Bearer x"})
        toks, inter = [], {}
        for line in r.text.splitlines():
            if line.startswith("data: "):
                try:
                    d = json.loads(line[6:])
                except ValueError:
                    continue
                toks.append(d.get("token") or "")
                inter = d.get("interaction") or inter
        out.append({"text": "".join(toks), "interaction": inter})
    return out


@pytest.fixture(scope="module", params=["send", "stream"])
def replay(request):
    os.environ["INTERACTION_CONTRACT_V1"] = "1"
    os.environ.setdefault("JWT_SECRET", "replay-test-secret-xxxxxxxxxxxxxxxxxxxxxxxx")
    mp = pytest.MonkeyPatch()
    cur = [0]
    try:
        _install(mp, cur)
        if request.param == "send":
            return asyncio.new_event_loop().run_until_complete(_replay_send(cur))
        return _replay_stream(cur)
    finally:
        mp.undo()
        os.environ.pop("INTERACTION_CONTRACT_V1", None)


_BUG = "Phase 0 — reproduces the production defect; fixed by the PR named in the reason"


@pytest.mark.xfail(strict=True, reason=_BUG + " (PR C: greetings must not assert scan/review state without a record)")
def test_turns_1_2_greeting_makes_no_unfounded_status_claim(replay):
    for t in (replay[0], replay[1]):
        assert not re.search(r"clean bill|checklist|ready for your review|scans?\b", t["text"], re.I)


@pytest.mark.xfail(strict=True, reason=_BUG + " (PR B: internal instructions must never leak)")
def test_turn_4_no_leaked_internal_instruction(replay):
    assert "short and simple" not in replay[3]["text"].lower()


@pytest.mark.xfail(strict=True, reason=_BUG + " (PR B: paths/PR words are never rewritten)")
def test_no_turn_replaces_a_path_with_that_file(replay):
    for t in replay[4:10]:
        assert "that file" not in t["text"].lower()
    assert "docs/OPERATIONS.md" in replay[8]["text"]


@pytest.mark.xfail(strict=True, reason=_BUG + " (PR B: a code block is never empty / left unclosed)")
def test_code_blocks_are_never_empty_or_unclosed(replay):
    for t in replay:
        text = t["text"]
        assert text.count("```") % 2 == 0
        for body in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
            assert re.sub(r"[\d.\s]", "", body), "block contains only empty numbered lines"


@pytest.mark.xfail(strict=True, reason=_BUG + " (PR A: narrated action with no tool result is not DONE)")
def test_promise_without_action_is_never_labelled_delivered(replay):
    for idx in (5, 9):
        assert replay[idx]["interaction"].get("completion_basis") != "response_delivered"


@pytest.mark.xfail(strict=True, reason=_BUG + " (PR C: drafting a file stores a pending change)")
def test_turn_11_ok_finds_the_pending_draft(replay):
    assert "don't have a change waiting" not in replay[10]["text"].lower()


@pytest.mark.xfail(strict=True, reason=_BUG + " (PR A: one outcome vocabulary, not 'Response delivered' for everything)")
def test_outcome_label_reflects_what_happened(replay):
    labels = [t["interaction"].get("completion_basis") for t in replay]
    assert labels.count("response_delivered") < len(labels) // 2
