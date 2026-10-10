"""
test_regression_iter399_bots_never_commit_to_pr_branches
===========================================================

Root cause (real incident, 2026-09-29): `auto-qa-bot` pushed
`chore: update auto-qa regression report [auto-qa-bot]` directly onto an
open PR's head branch (`ci/shard-backend-pytest-and-fix-live-url-timeout`,
commit `02511bc4`) mid-run, moving the PR's head SHA and forcing a rebase
before the branch could be pushed again. `delete-gate-bot` has the exact
same shape of bug (`git push origin "HEAD:$PUSH_REF"` where `$PUSH_REF` is
the PR's own head branch for `pull_request` events).

Both bots must still record their output somewhere durable (the founder's
admin QA dashboard reads `.emergent/latest-qa-report.md` off the `main`
branch's filesystem; `docs/DELETE_GATE.md` is a persistent audit log other
delete-gate runs check for a manual verdict) — so the fix is not "stop
writing the report", it is "never push it back onto a branch that is (or
could become) a PR head". `main` is never a PR's head branch, so committing
there is unaffected; every other branch/PR now gets the same content as a
PR comment instead.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTO_QA_YML = REPO_ROOT / ".github" / "workflows" / "auto-qa.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _steps(path, job_name):
    with open(path) as f:
        workflow = yaml.safe_load(f)
    return workflow["jobs"][job_name]["steps"]


def _step(steps, name_substring):
    for step in steps:
        if name_substring.lower() in str(step.get("name", "")).lower():
            return step
    return None


def test_regression_iter399_autoqa_bot_only_commits_on_main():
    steps = _steps(AUTO_QA_YML, "regression-locks")
    commit_step = _step(steps, "commit report back to repo")
    assert commit_step is not None
    condition = str(commit_step.get("if", ""))
    assert "ref_name == 'main'" in condition, (
        "auto-qa-bot's commit-back step must be restricted to the main "
        "branch — main is never a PR head, so a push there cannot move a "
        "PR's head SHA or cancel an in-flight required check"
    )


def test_regression_iter399_autoqa_bot_comments_on_non_main_branches():
    steps = _steps(AUTO_QA_YML, "regression-locks")
    comment_step = _step(steps, "post report as a pr comment")
    assert comment_step is not None, (
        "auto-qa-bot must have a step that posts the report as a PR "
        "comment for branches other than main, replacing the old commit"
    )
    condition = str(comment_step.get("if", ""))
    assert "ref_name != 'main'" in condition
    script = comment_step.get("run", "")
    assert "git push" not in script, (
        "the PR-comment fallback must never push to the branch"
    )
    assert "gh pr comment" in script


def test_regression_iter399_delete_gate_bot_skips_commit_on_pull_request():
    steps = _steps(CI_YML, "delete-gate")
    gate_step = _step(steps, "block undocumented file deletions")
    assert gate_step is not None
    script = gate_step.get("run", "")
    assert (
        'if [ "$EVENT" = "pull_request" ]' in script
        and "Posting the auto-verdict as a PR comment" in script
    ), (
        "delete-gate-bot must short-circuit before `git push` when the "
        "triggering event is pull_request — pushing to $PUSH_REF (the "
        "PR's own head branch) moves the PR head and cancels the "
        "in-flight run"
    )


def test_regression_iter399_delete_gate_bot_comments_on_pull_requests():
    steps = _steps(CI_YML, "delete-gate")
    comment_step = _step(steps, "post auto-verdict as a pr comment")
    assert comment_step is not None, (
        "delete-gate-bot must have a step that posts the auto-verdict as "
        "a PR comment for pull_request events, replacing the old commit"
    )
    condition = str(comment_step.get("if", ""))
    assert "pull_request" in condition
    script = comment_step.get("run", "")
    assert "git push" not in script
    assert "gh pr comment" in script


def test_regression_iter399_both_workflows_have_pull_requests_write_permission():
    for path, job_name in [(AUTO_QA_YML, "regression-locks"), (CI_YML, "delete-gate")]:
        steps = _steps(path, job_name)
        with open(path) as f:
            workflow = yaml.safe_load(f)
        perms = workflow["jobs"][job_name].get("permissions", {})
        assert perms.get("pull-requests") == "write", (
            f"{path.name}:{job_name} needs pull-requests: write to post PR "
            "comments via `gh pr comment`"
        )
