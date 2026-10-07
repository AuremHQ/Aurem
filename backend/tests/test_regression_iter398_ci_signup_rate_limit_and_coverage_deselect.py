"""
test_regression_iter398_ci_signup_rate_limit_and_coverage_deselect
====================================================================

Root cause (2026-09-29 quality-gate.yml "Fitness-function invariants" run,
job id 5322693): the invariants job boots one FastAPI process and then runs
~800 test functions against it inside a single job. Because
`SIGNUP_RATE_LIMIT_PER_IP` defaults to 3/24h (services/signup_guards.py),
the third test-suite signup from that job's single container IP got a 429,
and every later test that depended on that fixture's token cascaded into
~24 unrelated failures/errors (test_isolation_exploit.py,
test_loop_gate_chat_stream_2026_01.py, test_loop_gate_killswitch_2026_01b.py,
test_loop_gate_parity_and_mode_d_2026_01.py, test_rollback_v2_pillar1.py,
test_optional_phone_signup_2026_01.py, test_iter212m55_e2e_regression.py,
test_section15_batch_live_2026_09_22.py, ...). This is a CI-environment
config gap, not an application bug: the app-level rate limit is correct
and must not be loosened for real users.

Separately, test_regression_iter289_track1_lane_a.py::
test_frontend_coverage_summary_written asserts that
/app/frontend/coverage/coverage-summary.json exists. That file is only
produced by `npx vitest run --coverage`, which the invariants job never
runs (it has no Node/yarn install step, by design -- it is a backend-only
job). Its sibling test_backend_coverage_json_written was already
--deselect-ed for the same reason (see the existing comment in
quality-gate.yml); this test closes the same gap for the frontend half.

The fix (this PR) sets SIGNUP_RATE_LIMIT_PER_IP=1000 in the "Boot backend
in background" step of the invariants job, and adds a matching
--deselect for test_frontend_coverage_summary_written in the "Run
regression + invariant tests" step. This test guards both lines by
reading the workflow YAML directly, so a future edit that silently
removes either line fails CI instead of resurfacing the cascade.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "quality-gate.yml"


def _invariants_job():
    with open(WORKFLOW_PATH) as f:
        raw = f.read()
    workflow = yaml.safe_load(raw)
    jobs = workflow.get("jobs", {})
    assert "invariants" in jobs, "quality-gate.yml must still have an 'invariants' job"
    return jobs["invariants"], raw


def _step(job, name_substring):
    for step in job.get("steps", []):
        if name_substring.lower() in str(step.get("name", "")).lower():
            return step
    return None


def test_regression_iter398_invariants_job_sets_high_signup_rate_limit():
    job, _raw = _invariants_job()
    boot_step = _step(job, "boot backend")
    assert boot_step is not None, "invariants job must have a 'Boot backend' step"
    env = boot_step.get("env", {}) or {}
    assert "SIGNUP_RATE_LIMIT_PER_IP" in env, (
        "invariants job's boot step must set SIGNUP_RATE_LIMIT_PER_IP so the "
        "~800-test single-IP suite run does not 429 its own fixtures and "
        "cascade into unrelated failures"
    )
    value = int(str(env["SIGNUP_RATE_LIMIT_PER_IP"]))
    assert value >= 100, (
        "SIGNUP_RATE_LIMIT_PER_IP must be high enough that a single CI job "
        "with hundreds of signup-fixture calls from one IP never hits it"
    )


def test_regression_iter398_frontend_coverage_test_is_deselected():
    job, raw = _invariants_job()
    run_step = _step(job, "run regression")
    assert run_step is not None, "invariants job must have a 'Run regression + invariant tests' step"
    run_script = run_step.get("run", "")
    assert (
        "test_regression_iter289_track1_lane_a.py::test_frontend_coverage_summary_written"
        in run_script
    ), (
        "the invariants job has no Node/yarn install step and cannot ever "
        "produce frontend/coverage/coverage-summary.json, so "
        "test_frontend_coverage_summary_written must stay --deselect-ed here "
        "(its sibling test_backend_coverage_json_written already is, for the "
        "same reason)"
    )
