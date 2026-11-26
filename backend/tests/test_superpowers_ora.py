"""
tests/test_superpowers_ora.py — Superpowers (obra/superpowers v7.0.2,
pinned 22f0b463) vendored into Ora, Option A2 (2026-11-26).

Covers: all 14 skills load; keyword rules fire from each SKILL.md
front-matter description; the using-superpowers bootstrap is always
injected at task start; the shared 3-pick cap still holds; the
exclude param (repo-add dedup); the repo-add workflow block; the
rewordings + scripts/ adaptations in the vendored copies; and the
repo-add trigger firing exactly once on a project's first task
(fake DB + mocked GitHub fetch, same pattern as
test_h3_b1_direct_task_pin_2026_08_30.py).

Offline unit tests — real `.agents/skills/*/SKILL.md` files seeded
in tree/.agents/skills. Run from backend/:
    python -m pytest tests/test_superpowers_ora.py -q
    (with proxy vars cleared on this VM — see AGENTS.md)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from services import agent_skills_injector as asi  # noqa: E402
from services.skill_context_injector import build_skill_context  # noqa: E402

_SKILLS_DIR = _BACKEND_DIR.parent / ".agents" / "skills"

SUPERPOWERS_SKILLS = [
    "brainstorming",
    "diagnosing-superpowers",
    "dispatching-parallel-agents",
    "executing-plans",
    "finishing-a-development-branch",
    "receiving-code-review",
    "requesting-code-review",
    "subagent-driven-development",
    "systematic-debugging",
    "test-driven-development",
    "using-git-worktrees",
    "using-superpowers",
    "verification-before-completion",
    "writing-plans",
]


def _picked_names(task: str) -> list[str]:
    return [name for name, _ in asi.select_agent_skills(task)]


def test_all_14_superpowers_skills_load():
    """Every vendored Superpowers SKILL.md loads a non-empty slice —
    the files on disk are the ones the injector reads."""
    for skill in SUPERPOWERS_SKILLS:
        path = _SKILLS_DIR / skill / "SKILL.md"
        assert path.is_file(), f"missing vendored skill file: {path}"
        content = asi._load_agent_skill(skill, 2000)
        assert content, f"skill loads empty: {skill}"
        assert len(content) > 100, f"skill slice suspiciously short: {skill}"


@pytest.mark.parametrize(
    "task,skill",
    [
        ("brainstorm a new feature for the dashboard", "brainstorming"),
        ("write a plan for this multi-step task", "writing-plans"),
        ("implement a feature test first", "test-driven-development"),
        ("execute the plan step by step", "executing-plans"),
        ("debug this failing test, it is not working", "systematic-debugging"),
        ("verify the fix before committing", "verification-before-completion"),
        ("request a code review before merging", "requesting-code-review"),
        ("the reviewer said the approach is wrong, address the review feedback",
         "receiving-code-review"),
        ("finish the branch, ready to merge, open a pull request",
         "finishing-a-development-branch"),
        ("delegate this to a subagent", "subagent-driven-development"),
        ("work on these independent tasks in parallel",
         "dispatching-parallel-agents"),
        ("start feature work in an isolated workspace with a worktree",
         "using-git-worktrees"),
        ("the session went wrong, it took too long, what is it doing",
         "diagnosing-superpowers"),
    ],
)
def test_keyword_rules_fire_from_front_matter_descriptions(task, skill):
    """Each skill's keyword rule comes from its own SKILL.md
    front-matter description — a task phrased that way picks it up."""
    assert skill in _picked_names(task), (
        f"task {task!r} should pick {skill}; got {_picked_names(task)}")


def test_bootstrap_always_present_at_task_start():
    """using-superpowers is the bootstrap: it leads every task's
    skill block, even for a task that matches no keyword — Ora has
    no SessionStart hook, so task start is the bootstrap point."""
    ctx = asi.build_agent_skill_context("write a poem about cats")
    assert "SUPERPOWERS BOOTSTRAP" in ctx
    assert "### USING SUPERPOWERS" in ctx
    # Bootstrap is a separate block, not a keyword pick.
    assert "using-superpowers" not in _picked_names("write a poem about cats")


def test_max_three_keyword_skills_still_holds():
    """The shared 3-pick cap survives the Superpowers rules: a task
    matching many skills still gets at most 3 keyword picks (plus
    the security-trust always-inject)."""
    picks = asi.select_agent_skills(
        "brainstorm a new feature, write a plan for this multi-step "
        "task, implement a feature test first, execute the plan, debug "
        "the failing test, verify the fix before committing, and "
        "improve SEO Search Console traffic")
    keyword_picks = [n for n, _ in picks if n != "security-trust"]
    assert len(keyword_picks) <= 3


def test_exclude_param_omits_workflow_skills():
    """The repo-add first task excludes the 4 workflow skills from
    the keyword loop (they arrive via the workflow block instead) —
    but the bootstrap and the security baseline still fire."""
    task = ("brainstorm a new feature, write a plan for this "
            "multi-step task, implement a feature test first, verify "
            "the fix before committing")
    ctx = asi.build_agent_skill_context(
        task, exclude_agent_skills=set(asi.REPO_ADD_WORKFLOW_SKILLS))
    for title in ("### BRAINSTORMING", "### WRITING PLANS",
                  "### TEST DRIVEN DEVELOPMENT",
                  "### VERIFICATION BEFORE COMPLETION"):
        assert title not in ctx, f"excluded skill still injected: {title}"
    assert "SUPERPOWERS BOOTSTRAP" in ctx
    assert "### SECURITY TRUST" in ctx


def test_repo_add_workflow_block_composition():
    """The first-task block composes brainstorm → plan → TDD →
    verify from the vendored skills, with the Ora shipping rules
    (draft PR, approval gates, executing-plans over subagents)."""
    ctx = asi.build_repo_add_workflow_context()
    assert "NEW REPO ADDED" in ctx
    assert "### BRAINSTORMING" in ctx
    assert "### WRITING PLANS" in ctx
    assert "### TEST DRIVEN DEVELOPMENT" in ctx
    assert "### VERIFICATION BEFORE COMPLETION" in ctx
    assert "draft PR" in ctx
    assert "approval gates" in ctx
    assert "executing-plans over subagent-driven-development" in ctx


def test_build_skill_context_passes_exclude_through():
    """The exclude param threads through build_skill_context (the
    function all 4 call sites use): the Vanguard block still fires,
    the excluded workflow skill does not, the bootstrap does."""
    ctx = build_skill_context(
        "brainstorm a new feature and fix the security headers",
        exclude_agent_skills=set(asi.REPO_ADD_WORKFLOW_SKILLS))
    assert "VANGUARD SECURITY SKILLS" in ctx
    assert "### BRAINSTORMING" not in ctx
    assert "SUPERPOWERS BOOTSTRAP" in ctx


def test_rewordings_and_script_adaptations_in_vendored_copies():
    """Guards the A2 rewordings + the 8 scripts/ adaptations from
    being silently reverted by a future re-vendor: the flagged
    strings are gone, their replacements are present, and no
    scripts/ invocation remains in either SKILL.md."""
    ep = (_SKILLS_DIR / "executing-plans" / "SKILL.md").read_text()
    sdd = (_SKILLS_DIR / "subagent-driven-development" / "SKILL.md").read_text()
    for text in (ep, sdd):
        assert "git clean -fdx" not in text
        assert "scripts/" not in text
        assert "draft-PR branch" in text
    rc = (_SKILLS_DIR / "diagnosing-superpowers" / "prompts"
          / "request-conflicts.md").read_text()
    assert "Do not judge whether your human partner was right" not in rc
    assert "Do not take sides on whether your human partner was right" in rc
    did = (_SKILLS_DIR / "systematic-debugging"
           / "defense-in-depth.md").read_text()
    assert "Capture context for forensics" not in did
    assert "Record surrounding state for forensics" in did


# ── Repo-add trigger: first-task behaviour (fake DB) ──────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if row.get(k) != v:
                return False
        return True

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return types.SimpleNamespace(inserted_id="new")

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


@pytest.fixture
def _fake_db():
    from cto_services import db as _dbmod
    db = _FakeDB()
    _dbmod.set_db(db)
    yield db
    _dbmod.set_db(None)


async def _fake_fetch(owner, repo, path, ref, token):
    return "# widgets\n\nA test comment.\n"


@pytest.mark.asyncio
async def test_repo_add_workflow_fires_once_on_first_task(_fake_db):
    """A project with superpowers_first_task set gets the workflow
    block in its first task's prompt (each workflow skill exactly
    once — no keyword-loop duplicate), the flag is cleared, and a
    second task on the same project gets no workflow block."""
    from routers import cto_projects as router_mod
    from routers.cto_projects.worker_api import (
        _run_task_via_api_prepare_context,
    )

    proj = {
        "project_id": "p-sp1", "user_id": "u1",
        "github_owner": "acme", "github_repo": "widgets",
        "branch": "main", "tech_stack": "auto",
        "installation_id": "111",
        "superpowers_first_task": True,
    }
    await _fake_db.cto_projects.insert_one(dict(proj))
    await _fake_db.cto_tasks.insert_one(
        {"task_id": "task-sp1", "status": "queued", "started_at": 0})

    task = "brainstorm a new feature and write a plan for this multi-step task"
    with patch.object(router_mod, "gh_api_fetch_file",
                      AsyncMock(side_effect=_fake_fetch)):
        out1 = await _run_task_via_api_prepare_context(
            "task-sp1", "acme", "widgets", "main", ["README.md"],
            task, "", "ghp_faketoken789", proj, None, None, _fake_db)
    user_msg1 = out1[6]
    assert "NEW REPO ADDED" in user_msg1
    assert user_msg1.count("### BRAINSTORMING") == 1
    assert user_msg1.count("### WRITING PLANS") == 1
    row = await _fake_db.cto_projects.find_one({"project_id": "p-sp1"})
    assert row["superpowers_first_task"] is False

    # Second task: flag consumed, no workflow block.
    proj2 = dict(proj, superpowers_first_task=False)
    with patch.object(router_mod, "gh_api_fetch_file",
                      AsyncMock(side_effect=_fake_fetch)):
        out2 = await _run_task_via_api_prepare_context(
            "task-sp1", "acme", "widgets", "main", ["README.md"],
            task, "", "ghp_faketoken789", proj2, None, None, _fake_db)
    assert "NEW REPO ADDED" not in out2[6]


@pytest.mark.asyncio
async def test_no_workflow_block_without_flag(_fake_db):
    """Older project rows (no superpowers_first_task key) behave as
    False — no workflow block, no flag write, normal keyword picks
    still fire."""
    from routers import cto_projects as router_mod
    from routers.cto_projects.worker_api import (
        _run_task_via_api_prepare_context,
    )

    proj = {
        "project_id": "p-sp2", "user_id": "u1",
        "github_owner": "acme", "github_repo": "widgets",
        "branch": "main", "tech_stack": "auto",
        "installation_id": "111",
    }
    await _fake_db.cto_projects.insert_one(dict(proj))
    await _fake_db.cto_tasks.insert_one(
        {"task_id": "task-sp2", "status": "queued", "started_at": 0})

    task = "brainstorm a new feature and write a plan for this multi-step task"
    with patch.object(router_mod, "gh_api_fetch_file",
                      AsyncMock(side_effect=_fake_fetch)):
        out = await _run_task_via_api_prepare_context(
            "task-sp2", "acme", "widgets", "main", ["README.md"],
            task, "", "ghp_faketoken789", proj, None, None, _fake_db)
    user_msg = out[6]
    assert "NEW REPO ADDED" not in user_msg
    assert "### BRAINSTORMING" in user_msg  # keyword pick still fires
