"""
"Create a file" end to end through the real chat pipeline: a draft in the
reply becomes a stored pending change, "continue" shows it, "ok" opens a
branch + pull request (never the default branch) and returns the PR link.
Only the LLM, the repo read and the git adapter are stubbed.
Found by the ORA chat reliability replay (turns 6-11).
"""
import asyncio
from types import SimpleNamespace

import pytest

from services.actions import create_file_action as cfa

DRAFT = ("Here is the draft for docs/OPERATIONS.md, based on your repo. Assumption: you deploy with Docker.\n"
         "```markdown\n# Operations\n\n## Deploy\n- Build the image and push it.\n\n## Security\n"
         "- Rotate production credentials quarterly.\n```\nSay ok and I'll open a pull request.")
CASES = [  # (user message, LLM reply)
    ("docs/OPERATIONS.md build it", DRAFT),
    ("continue", "SHOULD NOT BE USED"),
    ("ok", "SHOULD NOT BE USED"),
    ("ok", "SHOULD NOT BE USED"),
]


class FakeGit:
    def __init__(self, fail_at=None):
        self.calls, self.fail_at = [], fail_at

    async def create_branch(self, base, new):
        self.calls.append(("branch", base, new)); return self.fail_at != "branch"

    async def commit(self, branch, files, message):
        self.calls.append(("commit", branch, dict(files))); return "" if self.fail_at == "commit" else "abc123"

    async def open_pr(self, head, base, title, body):
        self.calls.append(("pr", head, base))
        return None if self.fail_at == "pr" else {"url": "https://github.com/o/r/pull/7", "number": 7}


def _setup(monkeypatch, git):
    from tests._mini_motor import AsyncMongoMockClient

    import routers.chat as chat_mod
    import services.llm as llm
    import services.ora_context as ora_context
    import services.orchestrator as orch
    from cto_services.db import set_db

    db = AsyncMongoMockClient()["t"]
    set_db(db)
    cur = [0]
    bin_ctx = SimpleNamespace(bin_id="u1", repo_owner="o", repo_name="r", branch="main",
                              pat="tok", is_founder=False, pid="proj1")

    async def dev(*a, **k):
        return {"user_id": "u1", "email": "u@example.com", "tier": "pro"}

    async def noop(*a, **k):
        return None

    async def build(*a, **k):
        return bin_ctx

    async def read(ctx, args):
        return {"ok": False, "error": "File not found"}

    async def git_for(*a, **k):
        return git

    async def reply(*a, **k):
        return CASES[cur[0]][1]

    async def cwt(**k):
        return {"content": CASES[cur[0]][1], "provider": "stub", "tool_calls": [],
                "tool_invocations": [], "tool_calls_run": 0, "messages": []}

    monkeypatch.setattr(chat_mod.turn, "current_dev", dev)
    monkeypatch.setattr("services.usage.assert_has_budget", noop)
    monkeypatch.setattr("services.usage.assert_has_task_budget", noop)
    monkeypatch.setattr(ora_context, "build_ora_context", build)
    monkeypatch.setattr(chat_mod.turn, "get_repo_context", noop)
    monkeypatch.setattr("services.local_tools.read_repo_file", read)
    monkeypatch.setattr(cfa, "_git_for", git_for)
    monkeypatch.setattr(llm, "call_llm", reply)
    monkeypatch.setattr(orch, "chat_with_tools", cwt)
    monkeypatch.setattr(chat_mod.turn, "chat_with_tools", cwt)
    return db, cur, chat_mod.turn


async def _turn(turn_mod, cur, i):
    cur[0] = i
    return await turn_mod.chat_send(
        body=turn_mod.ChatBody(prompt=CASES[i][0], session_id="s1", project_id="proj1"),
        authorization="Bearer x")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_regression_iter415_draft_is_stored_continue_shows_it_ok_opens_a_pr(monkeypatch):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    git = FakeGit()
    db, cur, turn_mod = _setup(monkeypatch, git)

    async def go():
        out = [await _turn(turn_mod, cur, 0)]
        stored = await db.pending_actions.find({"status": "AWAITING_CONFIRM"}).to_list(10)
        for i in (1, 2, 3):
            out.append(await _turn(turn_mod, cur, i))
        return out, stored

    out, stored = _run(go())
    assert [(s["type"], s["payload"]["path"]) for s in stored] == [("create_file", "docs/OPERATIONS.md")]
    assert "```markdown" in out[0]["content"]                       # full draft shown once, intact

    cont = out[1]
    assert "docs/OPERATIONS.md" in cont["content"] and "Say ok" in cont["content"]
    assert cont["interaction"]["outcome"] == "NOTHING_DONE"          # showing a draft is not doing it

    ok = out[2]
    assert "https://github.com/o/r/pull/7" in ok["content"]
    assert ok["interaction"]["outcome"] == "DONE"
    kinds = [c[0] for c in git.calls]
    assert kinds == ["branch", "commit", "pr"]
    assert git.calls[0][2] != "main" and git.calls[1][1] == git.calls[0][2]     # never the default branch
    assert git.calls[1][2] == {"docs/OPERATIONS.md": stored[0]["payload"]["content"]}

    assert [c[0] for c in git.calls].count("pr") == 1                # a second "ok" opens nothing new


def test_regression_iter415_no_draft_means_no_stored_change_and_ok_says_so(monkeypatch):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    db, cur, turn_mod = _setup(monkeypatch, FakeGit())
    CASES[0] = ("docs/OPERATIONS.md build it", "I'll create docs/OPERATIONS.md with core guidelines. One moment.")
    try:
        async def go():
            first = await _turn(turn_mod, cur, 0)
            n = await db.pending_actions.count_documents({"status": "AWAITING_CONFIRM"})
            return first, n, await _turn(turn_mod, cur, 2)
        first, n, ok = _run(go())
    finally:
        CASES[0] = ("docs/OPERATIONS.md build it", DRAFT)
    assert n == 0 and first["interaction"]["reason"] == "announced_not_done"
    assert ok["interaction"]["outcome"] == "NOTHING_DONE" and ok["interaction"]["reason"] == "nothing_pending"


@pytest.mark.parametrize("fail_at,expect", [
    ("branch", "nothing was created"), ("commit", "not committed"), ("pr", "couldn't open the pull request")])
def test_regression_iter415_each_git_failure_is_reported_plainly(monkeypatch, fail_at, expect):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    git = FakeGit(fail_at=fail_at)
    db, cur, turn_mod = _setup(monkeypatch, git)

    async def go():
        await _turn(turn_mod, cur, 0)
        return await _turn(turn_mod, cur, 2)
    out = _run(go())
    assert expect in out["content"] and "pull/7" not in out["content"]
    later = {"branch": {"commit", "pr"}, "commit": {"pr"}, "pr": set()}[fail_at]
    assert not later & {c[0] for c in git.calls}          # a failed step stops the rest


@pytest.mark.parametrize("prompt,reply,expected", [
    ("docs/OPERATIONS.md build it", DRAFT, "docs/OPERATIONS.md"),
    ("create docs/NEW.md please", "ok\n```md\n" + "x" * 60 + "\n```", "docs/NEW.md"),
    ("what would docs/OPERATIONS.md look like", DRAFT, None),             # no creation request
    ("build me an ops guide", DRAFT, None),                                # user named no path
    ("docs/OPERATIONS.md build it", "```markdown\n1.\n2.\n3.\n```", None),  # empty draft
    ("docs/OPERATIONS.md build it", "```markdown\nshort\n```", None),       # too small to be a file
    ("docs/OPERATIONS.md build it", "I'll draft it now.", None),            # nothing to store
])
def test_regression_iter415_extract_create(prompt, reply, expected):
    out = cfa.extract_create(prompt, reply)
    assert (out or {}).get("path") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../etc/passwd.md", "/abs/x.md", ".git/config.txt",
                                  "tests/test_x.py", "src/__tests__/a.test.js"])
async def test_regression_iter415_unsafe_or_protected_paths_are_never_stored(monkeypatch, path):
    ok, _, reason = await cfa.validate_create(
        {"path": path, "content": "x" * 80}, ctx={"user_id": "u", "bin_ctx": object()})
    assert ok is False and reason in ("incomplete_or_unsafe_payload", "test_file_protected")


@pytest.mark.asyncio
async def test_regression_iter415_existing_or_unconfirmable_path_is_rejected(monkeypatch):
    async def exists(ctx, args):
        return {"ok": True, "content": "old"}

    async def broken(ctx, args):
        return {"ok": False, "error": "GitHub 502"}
    ctx = {"user_id": "u", "bin_ctx": object()}
    monkeypatch.setattr("services.local_tools.read_repo_file", exists)
    assert (await cfa.validate_create({"path": "a.md", "content": "x" * 80}, ctx=ctx))[2] == "file_already_exists"
    monkeypatch.setattr("services.local_tools.read_repo_file", broken)
    assert (await cfa.validate_create({"path": "a.md", "content": "x" * 80}, ctx=ctx))[2] == "could_not_confirm_path_is_new"
