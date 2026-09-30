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
