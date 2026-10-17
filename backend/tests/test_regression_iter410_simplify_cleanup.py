"""Regression lock for Ticket F (2026-02) — simplify/cleanup-only
mode.

Covers: the test/fixture refusal gate, the real-file import-
preservation proof (AGENTS.md section 9 / services/cto_projects_helpers.py
hallucination gate, reused verbatim — not a new detection path), scope-
creep refusal, a clean pass-through case, and the full end-to-end
preview pipeline + HTTP endpoint wiring with the LLM mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import simplify_pipeline as sp


# ── Real-file proof — an unrelated import would otherwise be dropped ──
def test_real_file_unrelated_import_drop_is_refused():
    """Uses the ACTUAL content of services/osv_client.py (a real file
    in this codebase, not a fixture/mock) as the original. Simulates
    what a naive "simplify" rewrite could easily do: keep the file
    working but drop the `cvss` import because the model judged it
    unused-looking at a glance — it is NOT unused (CVSS2/CVSS3 are
    real classes used later in the file). This is exactly the failure
    mode AGENTS.md section 9 calls out by name."""
    import inspect
    import services.osv_client as real_mod
    original = inspect.getsource(real_mod)
    assert "from cvss import CVSS2, CVSS3" in original  # sanity on the fixture itself

    bad_simplified = original.replace("from cvss import CVSS2, CVSS3\n", "")
    task_msg = sp.build_simplify_task_message("services/osv_client.py")

    violations = sp.check_simplify_edit(
        path="services/osv_client.py",
        new_content=bad_simplified,
        original_content=original,
        task_msg=task_msg,
    )
    assert violations, "dropping a real, still-used import must be caught"
    assert any("cvss" in v.lower() for v in violations)


def test_real_file_clean_simplify_with_all_imports_preserved_passes():
    """Same real file, but the 'simplified' version keeps every
    import — only a harmless whitespace/comment tweak. Must NOT be
    refused (proves the gate isn't just refusing everything)."""
    import inspect
    import services.osv_client as real_mod
    original = inspect.getsource(real_mod)
    clean_simplified = original + "\n# simplify: no dead code found\n"
    task_msg = sp.build_simplify_task_message("services/osv_client.py")

    violations = sp.check_simplify_edit(
        path="services/osv_client.py",
        new_content=clean_simplified,
        original_content=original,
        task_msg=task_msg,
    )
    assert violations == []


# ── Test/fixture refusal — immediate, before any LLM call ───────────
@pytest.mark.asyncio
async def test_refuses_test_file_before_calling_llm():
    with patch("services.llm.call_llm", AsyncMock()) as llm_spy:
        result = await sp.run_simplify_preview(
            owner="o", repo="r", branch="main", token="tok",
            path="backend/tests/test_something.py",
        )
    assert result["ok"] is False
    assert result["refused"] is True
    assert "test" in result["reason"].lower()
    llm_spy.assert_not_called()


@pytest.mark.asyncio
async def test_refuses_fixture_path_before_calling_llm():
    with patch("services.llm.call_llm", AsyncMock()) as llm_spy:
        result = await sp.run_simplify_preview(
            owner="o", repo="r", branch="main", token="tok",
            path="frontend/src/__tests__/App.test.jsx",
        )
    assert result["refused"] is True
    llm_spy.assert_not_called()


# ── End-to-end preview — LLM mocked, real gate logic ─────────────────
@pytest.mark.asyncio
async def test_preview_refuses_when_llm_drops_an_import():
    original = "import os\nimport sys\n\ndef f():\n    return os.path.join(sys.argv[0], 'x')\n"
    bad_reply = "FILE: a.py\n```\nimport os\n\ndef f():\n    return os.path.join('x')\n```"
    with patch("services.repo_context._fetch_file", AsyncMock(return_value=original)), \
         patch("services.llm.call_llm", AsyncMock(return_value=bad_reply)):
        result = await sp.run_simplify_preview(
            owner="o", repo="r", branch="main", token="tok", path="a.py",
        )
    assert result["ok"] is False
    assert result["refused"] is True
    assert "violations" in result
    assert any("sys" in v for v in result["violations"])


@pytest.mark.asyncio
async def test_preview_approves_a_genuinely_clean_simplify():
    original = "import os\n\ndef f():\n    unused_var = 1\n    return os.getcwd()\n"
    clean_reply = "FILE: a.py\n```\nimport os\n\ndef f():\n    return os.getcwd()\n```"
    with patch("services.repo_context._fetch_file", AsyncMock(return_value=original)), \
         patch("services.llm.call_llm", AsyncMock(return_value=clean_reply)):
        result = await sp.run_simplify_preview(
            owner="o", repo="r", branch="main", token="tok", path="a.py",
        )
    assert result["ok"] is True
    assert result["refused"] is False
    assert "unused_var" not in result["new_content"]
    assert "import os" in result["new_content"]
    assert result["diff"]  # a real unified diff was produced


@pytest.mark.asyncio
async def test_preview_honest_failure_when_file_read_fails():
    with patch("services.repo_context._fetch_file", AsyncMock(return_value=None)):
        result = await sp.run_simplify_preview(
            owner="o", repo="r", branch="main", token="tok", path="missing.py",
        )
    assert result["ok"] is False
    assert result["refused"] is False  # honest read-failure, not a refusal
    assert "could not read" in result["reason"]


@pytest.mark.asyncio
async def test_preview_honest_failure_when_llm_raises():
    with patch("services.repo_context._fetch_file", AsyncMock(return_value="x = 1\n")), \
         patch("services.llm.call_llm", AsyncMock(side_effect=RuntimeError("llm down"))):
        result = await sp.run_simplify_preview(
            owner="o", repo="r", branch="main", token="tok", path="a.py",
        )
    assert result["ok"] is False
    assert "codegen failed" in result["reason"]


# ── Behavior-change refusal — scope creep on an unrelated symbol ────
def test_unrelated_symbol_behavior_change_is_refused():
    original = (
        "def helper_a():\n    return 1\n\n"
        "def helper_b():\n    total = 0\n    for i in range(10):\n"
        "        total += i\n    return total\n"
    )
    # task only targets helper_a; helper_b's body is substantially
    # rewritten with no connection to the task at all — classic
    # scope creep (small 1-token tweaks stay under the gate's 90%
    # similarity threshold by design — this is a real rewrite, not
    # a cosmetic one, so it must trip the existing ratio check).
    bad_simplified = (
        "def helper_a():\n    return 1\n\n"
        "def helper_b():\n    return sum(range(10)) * 2\n"
    )
    task_msg = sp.build_simplify_task_message("a.py")
    violations = sp.check_simplify_edit(
        path="a.py", new_content=bad_simplified, original_content=original,
        task_msg=task_msg,
    )
    assert violations, "an unrelated symbol's behavior silently changing must be refused"


# ── HTTP endpoint wiring ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_endpoint_404_when_project_not_owned():
    from routers.cto_projects.management import simplify_file, SimplifyRequest
    fake_db = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value=None)
    with patch("routers.cto_projects.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.get_db", return_value=fake_db):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await simplify_file("p1", SimplifyRequest(path="a.py"), authorization="Bearer x")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_422_when_pipeline_refuses():
    from routers.cto_projects.management import simplify_file, SimplifyRequest
    fake_db = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1", "github_owner": "o",
        "github_repo": "r", "branch": "main",
    })
    with patch("routers.cto_projects.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.get_db", return_value=fake_db), \
         patch("routers.cto_projects.management.get_repo_token", AsyncMock(return_value="tok")), \
         patch("services.simplify_pipeline.run_simplify_preview",
               AsyncMock(return_value={"ok": False, "refused": True, "reason": "x"})):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await simplify_file("p1", SimplifyRequest(path="backend/tests/foo.py"),
                                 authorization="Bearer x")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_endpoint_424_when_codegen_fails_not_422():
    """2026-02 bug fix: a non-refused pipeline failure (LLM/codegen
    error, ok:false but refused:false) must surface with a status
    code whose body survives the edge/CDN layer in front of this app
    (confirmed live: a plain 502 gets its body replaced by Cloudflare
    before it reaches the browser — the honest `reason` never made it
    through), never silently as a 200 with a failure body either (the
    frontend was previously treating any 200 as "preview succeeded"
    and would have rendered an Apply button against an empty diff)."""
    from routers.cto_projects.management import simplify_file, SimplifyRequest
    fake_db = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1", "github_owner": "o",
        "github_repo": "r", "branch": "main",
    })
    with patch("routers.cto_projects.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.get_db", return_value=fake_db), \
         patch("routers.cto_projects.management.get_repo_token", AsyncMock(return_value="tok")), \
         patch("services.simplify_pipeline.run_simplify_preview",
               AsyncMock(return_value={"ok": False, "refused": False,
                                        "reason": "codegen failed: 401 Unauthorized"})):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await simplify_file("p1", SimplifyRequest(path="services/x.py"),
                                 authorization="Bearer x")
    assert exc.value.status_code == 424
    assert "codegen failed" in exc.value.detail["reason"]
#    never commits to the default branch ────────────────────────────
@pytest.mark.asyncio
async def test_apply_refuses_when_current_file_no_longer_matches_preview():
    """Defense in depth: even if the client replays an old 'approved'
    new_content, apply() re-fetches the CURRENT file and re-runs the
    gate — a real import dropped against the CURRENT content must
    still be refused, never trusted blindly from the client."""
    original_now = "import os\nimport sys\n\ndef f():\n    return os.path.join(sys.argv[0])\n"
    stale_new_content = "import os\n\ndef f():\n    return os.path.join('x')\n"  # drops sys
    with patch("services.repo_context._fetch_file", AsyncMock(return_value=original_now)):
        result = await sp.apply_simplify_edit(
            db=MagicMock(), user_id="u1", owner="o", repo="r", branch="main",
            token="tok", path="a.py", new_content=stale_new_content,
        )
    assert result["ok"] is False
    assert result["refused"] is True
    assert any("sys" in v for v in result["violations"])


@pytest.mark.asyncio
async def test_apply_refuses_test_file_before_any_github_call():
    from unittest.mock import AsyncMock as _AM
    with patch("services.repo_context._fetch_file", _AM()) as fetch_spy:
        result = await sp.apply_simplify_edit(
            db=MagicMock(), user_id="u1", owner="o", repo="r", branch="main",
            token="tok", path="backend/tests/test_x.py", new_content="x = 1\n",
        )
    assert result["refused"] is True
    fetch_spy.assert_not_called()


@pytest.mark.asyncio
async def test_apply_never_commits_to_the_default_branch_always_a_dedicated_branch():
    """The single most important safety property of Ticket F-UI's
    apply flow — asserts the ACTUAL branch name passed to commit_files
    is NOT 'main' (the project's default branch)."""
    original = "import os\n\ndef f():\n    unused = 1\n    return os.getcwd()\n"
    clean = "import os\n\ndef f():\n    return os.getcwd()\n"
    commit_calls = {}

    async def _fake_commit_files(**kwargs):
        commit_calls.update(kwargs)
        return {"ok": True, "sha": "deadbeef", "html_url": "https://github.com/o/r/commit/deadbeef"}

    with patch("services.repo_context._fetch_file", AsyncMock(return_value=original)), \
         patch("services.loop_safety.create_or_reuse_branch", AsyncMock(return_value=(True, None))), \
         patch("services.github_api_writer.commit_files", _fake_commit_files), \
         patch("services.git_identity.resolve_git_identity", AsyncMock(return_value=("Dev", "dev@x.com"))), \
         patch("services.loop_safety.open_or_reuse_pr",
               AsyncMock(return_value={"ok": True, "pr_url": "https://github.com/o/r/pull/1",
                                        "reused": False})):
        result = await sp.apply_simplify_edit(
            db=MagicMock(), user_id="u1", owner="o", repo="r", branch="main",
            token="tok", path="a.py", new_content=clean,
        )
    assert result["ok"] is True
    assert commit_calls["branch"] != "main"
    assert commit_calls["branch"] == result["branch"]
    assert result["pr_url"] == "https://github.com/o/r/pull/1"
    assert result["pr_error"] is None


@pytest.mark.asyncio
async def test_apply_pr_open_failure_still_reports_the_real_commit_honestly():
    """2026-02 fix: open_or_reuse_pr's own contract is 'ok=False means
    commit succeeded, PR step soft-failed' — apply_simplify_edit must
    NOT silently return pr_url=None with no explanation (the UI would
    render a blank 'view PR' link); it must still say ok:true (the
    commit really landed) but carry a pr_error the UI can show."""
    original = "import os\n\ndef f():\n    unused = 1\n    return os.getcwd()\n"
    clean = "import os\n\ndef f():\n    return os.getcwd()\n"
    with patch("services.repo_context._fetch_file", AsyncMock(return_value=original)), \
         patch("services.loop_safety.create_or_reuse_branch", AsyncMock(return_value=(True, None))), \
         patch("services.github_api_writer.commit_files",
               AsyncMock(return_value={"ok": True, "sha": "deadbeef", "html_url": "https://github.com/o/r/commit/deadbeef"})), \
         patch("services.git_identity.resolve_git_identity", AsyncMock(return_value=("Dev", "dev@x.com"))), \
         patch("services.loop_safety.open_or_reuse_pr",
               AsyncMock(return_value={"ok": False, "pr_url": None, "error": "GitHub API error: 403"})):
        result = await sp.apply_simplify_edit(
            db=MagicMock(), user_id="u1", owner="o", repo="r", branch="main",
            token="tok", path="a.py", new_content=clean,
        )
    assert result["ok"] is True  # the commit really landed
    assert result["pr_url"] is None
    assert result["pr_error"] == "GitHub API error: 403"


@pytest.mark.asyncio
async def test_apply_branch_create_failure_is_honest_not_silent():
    clean_from_original = "import os\n\ndef f():\n    return os.getcwd()\n"
    with patch("services.repo_context._fetch_file", AsyncMock(return_value="import os\n\ndef f():\n    x=1\n    return os.getcwd()\n")), \
         patch("services.loop_safety.create_or_reuse_branch",
               AsyncMock(return_value=(False, "base_ref_status_404"))):
        result = await sp.apply_simplify_edit(
            db=MagicMock(), user_id="u1", owner="o", repo="r", branch="main",
            token="tok", path="a.py", new_content=clean_from_original,
        )
    assert result["ok"] is False
    assert "branch_create_failed" in result["reason"]


@pytest.mark.asyncio
async def test_apply_unchanged_content_is_a_noop_no_github_calls():
    same = "import os\n\ndef f():\n    return os.getcwd()\n"
    with patch("services.repo_context._fetch_file", AsyncMock(return_value=same)), \
         patch("services.loop_safety.create_or_reuse_branch", AsyncMock()) as branch_spy:
        result = await sp.apply_simplify_edit(
            db=MagicMock(), user_id="u1", owner="o", repo="r", branch="main",
            token="tok", path="a.py", new_content=same,
        )
    assert result == {"ok": True, "refused": False, "unchanged": True}
    branch_spy.assert_not_called()


@pytest.mark.asyncio
async def test_apply_endpoint_wires_through_and_returns_pr_url():
    from routers.cto_projects.management import simplify_apply, SimplifyApplyRequest
    fake_db = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1", "github_owner": "o",
        "github_repo": "r", "branch": "main",
    })
    with patch("routers.cto_projects.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.get_db", return_value=fake_db), \
         patch("routers.cto_projects.management.get_repo_token", AsyncMock(return_value="tok")), \
         patch("services.simplify_pipeline.apply_simplify_edit",
               AsyncMock(return_value={"ok": True, "refused": False,
                                        "pr_url": "https://github.com/o/r/pull/9",
                                        "branch": "aurem/simplify-a-py"})):
        resp = await simplify_apply("p1", SimplifyApplyRequest(path="a.py", new_content="x=1\n"),
                                     authorization="Bearer x")
    assert resp["pr_url"] == "https://github.com/o/r/pull/9"


@pytest.mark.asyncio
async def test_apply_endpoint_422_when_gate_refuses():
    from routers.cto_projects.management import simplify_apply, SimplifyApplyRequest
    fake_db = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1", "github_owner": "o",
        "github_repo": "r", "branch": "main",
    })
    with patch("routers.cto_projects.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.get_db", return_value=fake_db), \
         patch("routers.cto_projects.management.get_repo_token", AsyncMock(return_value="tok")), \
         patch("services.simplify_pipeline.apply_simplify_edit",
               AsyncMock(return_value={"ok": False, "refused": True, "reason": "x"})):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await simplify_apply("p1", SimplifyApplyRequest(path="a.py", new_content="x=1\n"),
                                  authorization="Bearer x")
    assert exc.value.status_code == 422
