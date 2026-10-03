"""Deploy safety gate: every production deploy must wait for a manual
approval, and nothing may report a deploy that did not happen.

The checker below is exercised on synthetic good AND bad workflows (so a
regression in the checker itself is caught), then applied to the real
workflow files in .github/workflows/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
HOOK_NAMES = ("AUREM_DEPLOY_HOOK_URL", "VERCEL_DEPLOY_HOOK")
REPORT_ENDPOINT = "github-deploy/report"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _job_text(job: dict) -> str:
    return json.dumps(job)


def _env_name(job: dict):
    env = job.get("environment")
    return env.get("name") if isinstance(env, dict) else env


def ungated_deploy_jobs(workflow: dict) -> list[str]:
    """Jobs that touch a production deploy hook without the `production`
    environment (= without a required-reviewer approval)."""
    return [
        name for name, job in (workflow.get("jobs") or {}).items()
        if any(h in _job_text(job) for h in HOOK_NAMES) and _env_name(job) != "production"
    ]


def reports_deploy(workflow: dict) -> list[str]:
    return [name for name, job in (workflow.get("jobs") or {}).items()
            if REPORT_ENDPOINT in _job_text(job)]


def test_checker_flags_a_deploy_job_without_environment():
    bad = {"jobs": {"deploy": {"steps": [{"env": {"HOOK": "${{ secrets.AUREM_DEPLOY_HOOK_URL }}"}}]}}}
    assert ungated_deploy_jobs(bad) == ["deploy"]


def test_checker_accepts_gated_jobs_in_both_yaml_forms():
    s = {"steps": [{"env": {"H": "secrets.VERCEL_DEPLOY_HOOK"}}]}
    assert ungated_deploy_jobs({"jobs": {"d": {**s, "environment": "production"}}}) == []
    assert ungated_deploy_jobs({"jobs": {"d": {**s, "environment": {"name": "production"}}}}) == []
    assert ungated_deploy_jobs({"jobs": {"d": {**s, "environment": "staging"}}}) == ["d"]


@pytest.mark.parametrize("name", sorted(p.name for p in WORKFLOWS.glob("*.yml")))
def test_no_workflow_calls_a_deploy_hook_without_approval(name):
    assert ungated_deploy_jobs(_load(name)) == [], f"{name}: deploy hook used outside the 'production' environment"


def test_the_two_real_deploy_workflows_are_actually_gated():
    for name, job in (("auto_deploy.yml", "deploy-and-report"), ("deploy.yml", "deploy")):
        assert _env_name(_load(name)["jobs"][job]) == "production"


def test_gate_on_ci_still_precedes_the_approval_in_auto_deploy():
    jobs = _load("auto_deploy.yml")["jobs"]
    assert "gate-on-ci" in jobs["deploy-and-report"]["needs"]


@pytest.mark.parametrize("name", sorted(p.name for p in WORKFLOWS.glob("*.yml")))
def test_only_auto_deploy_may_report_a_deploy_result(name):
    reporters = reports_deploy(_load(name))
    assert reporters == [] or name == "auto_deploy.yml", f"{name}: reports a deploy from {reporters}"


def test_auto_deploy_never_defaults_the_reported_result_to_success():
    steps = _load("auto_deploy.yml")["jobs"]["deploy-and-report"]["steps"]
    report = next(s for s in steps if s.get("name") == "Report to AUREM")
    assert "steps.trigger.outputs.result != ''" in report["if"]
    assert "|| 'success'" not in json.dumps(report)
