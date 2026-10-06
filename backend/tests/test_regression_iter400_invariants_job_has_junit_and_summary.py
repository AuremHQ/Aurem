"""
test_regression_iter400_invariants_job_has_junit_and_summary
================================================================

Item 4 of the CI-trustworthiness pass: the "Fitness-function invariants"
job in quality-gate.yml had no JUnit XML artifact and no failing-test
summary — unlike ci.yml's "Backend — pytest" job, which has carried a
"Summarise failing tests" step since PR #191. This made diagnosing a red
invariants run require downloading and manually paging through the raw
step log. This test guards the fix: --junitxml on the pytest invocation,
a PIPESTATUS-safe exit-code capture around the `| tee` pipe (the same trap
ci.yml's own "Run tests" step comments on — a naive `cmd | tee f` swallows
a real failure's exit code), a "Summarise failing tests" step, and an
"Upload JUnit XML artifact" step that both run with `if: always()` so they
still produce output when the tests themselves are red.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
QUALITY_GATE_YML = REPO_ROOT / ".github" / "workflows" / "quality-gate.yml"


def _invariants_steps():
    with open(QUALITY_GATE_YML) as f:
        workflow = yaml.safe_load(f)
    return workflow["jobs"]["invariants"]["steps"]


def _step(steps, name_substring):
    for step in steps:
        if name_substring.lower() in str(step.get("name", "")).lower():
            return step
    return None


def test_regression_iter400_run_step_writes_junit_xml():
    steps = _invariants_steps()
    run_step = _step(steps, "run regression + invariant tests")
    assert run_step is not None
    script = run_step.get("run", "")
    assert "--junitxml=" in script, (
        "the invariants job's test-run step must produce a JUnit XML file"
    )


def test_regression_iter400_run_step_does_not_swallow_pytest_exit_code():
    steps = _invariants_steps()
    run_step = _step(steps, "run regression + invariant tests")
    script = run_step.get("run", "")
    assert "set -o pipefail" in script or "PIPESTATUS" in script, (
        "piping pytest through `tee` without pipefail/PIPESTATUS makes "
        "the step exit 0 (tee's own exit code) even when pytest failed"
    )


def test_regression_iter400_summarise_failing_tests_step_exists_and_always_runs():
    steps = _invariants_steps()
    summary_step = _step(steps, "summarise failing tests")
    assert summary_step is not None, (
        "invariants job must have the same 'Summarise failing tests' "
        "step ci.yml's Backend pytest job has had since PR #191"
    )
    assert str(summary_step.get("if", "")) == "always()", (
        "the summary must still run when the test step itself failed"
    )
    script = summary_step.get("run", "")
    assert "FAILED|ERROR" in script
    assert "GITHUB_STEP_SUMMARY" in script


def test_regression_iter400_junit_artifact_upload_step_exists_and_always_runs():
    steps = _invariants_steps()
    upload_step = _step(steps, "upload junit xml artifact")
    assert upload_step is not None
    assert str(upload_step.get("if", "")) == "always()", (
        "the JUnit XML must still be uploaded when the test step failed "
        "-- that's the run a human most needs to inspect"
    )
    assert upload_step.get("uses", "").startswith("actions/upload-artifact")


def test_regression_iter400_step_order_summary_and_upload_after_run_before_stop():
    steps = _invariants_steps()
    names = [str(s.get("name", "")) for s in steps]
    run_idx = next(i for i, n in enumerate(names) if n.lower().startswith("run regression"))
    summary_idx = next(i for i, n in enumerate(names) if "summarise failing" in n.lower())
    upload_idx = next(i for i, n in enumerate(names) if "upload junit" in n.lower())
    stop_idx = next(i for i, n in enumerate(names) if n.lower() == "stop backend")
    assert run_idx < summary_idx < stop_idx
    assert run_idx < upload_idx < stop_idx
