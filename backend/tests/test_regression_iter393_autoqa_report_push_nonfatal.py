"""Regression — a failed QA-report push must not fail the Auto-QA gate.

`Auto-QA — Regression Library Gate` went red on main and on PRs with every
regression lock passing: only its last step ("Commit report back to repo")
failed, because the push is rejected when the branch moved during the run
or is protected. The step's real script is exercised here against a
temporary git remote.
"""
import os
import stat
import subprocess
from pathlib import Path

import yaml

AUTO_QA_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "auto-qa.yml"


def _report_step_script():
    jobs = yaml.safe_load(AUTO_QA_YML.read_text(encoding="utf-8"))["jobs"]
    steps = jobs["regression-locks"]["steps"]
    step = next(s for s in steps if str(s.get("name", "")).startswith("Commit report back to repo"))
    return step["run"]


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo_with_remote(tmp_path, reject_pushes):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(remote), str(work))
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@example.com")
    (work / ".emergent").mkdir()
    report = work / ".emergent" / "latest-qa-report.md"
    report.write_text("old\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "origin", "HEAD:main")
    if reject_pushes:
        hook = remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'protected branch' >&2\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    report.write_text("new report\n")
    return work, remote


def _run_step(work):
    return subprocess.run(
        ["bash", "-e", "-c", _report_step_script()],
        cwd=work, capture_output=True, text=True, check=False,
        env={**os.environ, "GITHUB_REF": "refs/heads/main"},
    )


def test_regression_iter393_rejected_report_push_warns_but_does_not_fail(tmp_path):
    work, _ = _repo_with_remote(tmp_path, reject_pushes=True)
    proc = _run_step(work)
    assert proc.returncode == 0, proc.stderr
    assert "::warning::" in proc.stdout


def test_regression_iter393_report_is_still_pushed_when_the_remote_accepts(tmp_path):
    work, remote = _repo_with_remote(tmp_path, reject_pushes=False)
    proc = _run_step(work)
    assert proc.returncode == 0, proc.stderr
    assert "::warning::" not in proc.stdout
    log = _git(remote, "log", "--format=%s", "-1", "main").stdout
    assert "update auto-qa regression report" in log
