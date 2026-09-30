"""Regression — files in live_env_quarantine.txt must run somewhere in CI.

They skip in ci.yml (no server there, and it must not point at the real
deployment). If no job boots a backend and selects `-m live_env`, they run
nowhere and the quarantine list silently becomes a list of disabled tests.
"""
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "quality-gate.yml"


def _job():
    return yaml.safe_load(WF.read_text())["jobs"]["live-env-tests"]


def _run_scripts(job):
    return "\n".join(s.get("run", "") for s in job["steps"])


def test_regression_iter401_live_env_job_boots_a_backend_and_selects_live_env():
    job = _job()
    scripts = _run_scripts(job)
    assert "uvicorn main:app" in scripts
    assert "-m live_env" in scripts


def test_regression_iter401_live_env_job_points_the_tests_at_the_local_backend():
    job = _job()
    urls = [
        s["env"]["REACT_APP_BACKEND_URL"]
        for s in job["steps"]
        if "-m live_env" in s.get("run", "")
    ]
    assert urls == ["http://localhost:8001"]


def test_regression_iter401_live_env_job_keeps_the_real_pytest_exit_code():
    scripts = _run_scripts(_job())
    assert "set -o pipefail" in scripts
    assert "PIPESTATUS[0]" in scripts


def test_regression_iter401_live_env_job_reports_failures_even_when_red():
    steps = {s.get("name"): s for s in _job()["steps"]}
    assert steps["Summarise failing tests"]["if"] == "always()"
    assert steps["Upload JUnit XML artifact"]["if"] == "always()"


def test_regression_iter401_failing_test_list_is_also_posted_on_the_pr():
    """The job summary is not readable through the API and the raw log tail is
    service-container noise, so both test jobs must also comment the list."""
    jobs = yaml.safe_load(WF.read_text())["jobs"]
    for name in ("invariants", "live-env-tests"):
        job = jobs[name]
        assert job["permissions"]["pull-requests"] == "write"
        step = next(s for s in job["steps"] if s.get("name") == "Summarise failing tests")
        assert "gh pr comment" in step["run"]
        assert step["if"] == "always()"


def test_regression_iter401_shard_jobs_can_comment_their_failing_tests():
    ci = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    job = yaml.safe_load(ci.read_text())["jobs"]["backend-tests"]
    assert job["permissions"]["pull-requests"] == "write"
    step = next(s for s in job["steps"] if s.get("name", "").startswith("Summarise failing tests"))
    assert "gh pr comment" in step["run"]


def test_regression_iter401_files_hardcoding_port_8001_are_quarantined_or_run_in_a_booted_job():
    """A test file that hardcodes :8001 can only pass where a server runs. It must
    be in the quarantine list (skips in the shards, runs in live-env-tests) or be
    picked up by the invariants job, never left to fail `Connection refused`."""
    import re

    tests_dir = Path(__file__).resolve().parent
    listed = {
        Path(line.strip().split("::")[0]).name
        for line in (tests_dir / "live_env_quarantine.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    # The invariants job selects tests by name (`-k`); jwt_revocation is one of them.
    runs_in_invariants = {"test_jwt_revocation.py"}
    offenders = sorted(
        p.name
        for p in tests_dir.rglob("*.py")
        if p.name != Path(__file__).name
        and re.search(r"(localhost|127\.0\.0\.1):8001", p.read_text(errors="ignore"))
        and p.name not in listed
        and p.name not in runs_in_invariants
    )
    assert offenders == []
